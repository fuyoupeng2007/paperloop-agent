"""Bounded, resumable research agent driven by observations and explicit tools."""
import hashlib
import json
import math
import re
import threading
import time
import uuid

from fastapi import HTTPException

from . import provider, research, store

LOCK = threading.RLock()
ACTIVE = {}  # document id -> (run id, execution token)
ACTIONS = {'search_paper', 'read_blocks', 'inspect_figure', 'web_search', 'finish'}
TERMINAL = {'completed', 'cancelled'}


class Halt(Exception):
    pass


class RunBudget(Exception):
    pass


class ProviderChanged(Exception):
    pass


def _text(value, limit=2000):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _strings(value, limit=12, width=500):
    return [_text(x, width) for x in value if _text(x, width)][:limit] if isinstance(value, list) else []


def _fingerprint():
    config = store.settings()
    selected = {key: config.get(key, '') for key in ('provider', 'base_url', 'model', 'vision_model', 'api_key')}
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def _tools():
    from . import agent_tools
    return agent_tools


def _change(run_id, token, operation):
    def mutate(run):
        if run.get('execution_token') != token:
            raise Halt()
        operation(run)
        run['updated_at'] = time.time()
    return store.agent_change(run_id, mutate)


def _checkpoint(run_id, token):
    run = store.agent_get(run_id)
    if run.get('execution_token') != token or run['status'] != 'running':
        raise Halt()
    if run.get('cancel_requested') or run.get('pause_requested'):
        cancelled = bool(run.get('cancel_requested'))
        def settle(current):
            current.update(status='cancelled' if cancelled else 'paused',
                           stop_reason='cancelled' if cancelled else 'paused',
                           pause_requested=False, cancel_requested=False,
                           error='已取消，已有证据和进度保留。' if cancelled else '已暂停，恢复后从已保存证据继续。')
        _change(run_id, token, settle)
        raise Halt()
    return run


def _reserve(run_id, token):
    _checkpoint(run_id, token)
    if _fingerprint() != store.agent_get(run_id)['provider_fingerprint']:
        raise ProviderChanged('模型连接已改变。请恢复原连接后继续，或新建研究任务。')
    def increment(run):
        if run['status'] != 'running' or run.get('pause_requested') or run.get('cancel_requested'):
            raise Halt()
        if run['calls_used'] >= run['max_calls']:
            raise RunBudget('已达到本次研究的模型请求上限。')
        run['calls_used'] += 1
    _change(run_id, token, increment)


def _model(run_id, token, task, payload, instruction):
    run = _checkpoint(run_id, token)
    _reserve(run_id, token)
    _checkpoint(run_id, token)
    result = provider.complete(run['doc_id'], [
        {'role': 'system', 'content': provider.GUARD + ' ' + instruction},
        {'role': 'user', 'content': json.dumps({'task': task, **payload}, ensure_ascii=False)}], max_tokens=5000)
    _checkpoint(run_id, token)
    return provider.json_response(result)


def _box(box):
    return (isinstance(box, list) and len(box) == 4 and
            all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= 1 for v in box) and
            box[0] < box[2] and box[1] < box[3])


def _evidence(doc, value):
    """Re-check source identity and literal paper quotations at the boundary."""
    if not isinstance(value, dict):
        return None
    identity, kind, quote = _text(value.get('id'), 160), value.get('type'), _text(value.get('quote'), 1800)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', identity) or kind not in ('paper', 'visual', 'web') or not quote:
        return None
    result = {'id': identity, 'type': kind, 'title': _text(value.get('title'), 400), 'quote': quote}
    if kind == 'paper':
        block = next((b for b in doc['blocks'] if b['id'] == value.get('block_id')), None)
        if not block or ' '.join(quote.split()) not in ' '.join(block['text'].split()):
            return None
        result.update(block_id=block['id'], page=block['page'])
    elif kind == 'visual':
        page = value.get('page')
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= doc['page_count'] or not _box(value.get('box')):
            return None
        result.update(page=page, box=value['box'], object_id=_text(value.get('object_id'), 160))
    else:
        url = _text(value.get('url'), 2000)
        if not research.public_source(url):
            return None
        result['url'] = url
    return result


def _decision(value):
    if not isinstance(value, dict) or value.get('action') not in ACTIONS:
        raise provider.ProviderError('研究决策格式错误，需重新选择允许的动作。')
    claims = []
    for claim in value.get('claims', []) if isinstance(value.get('claims'), list) else []:
        if isinstance(claim, dict) and _text(claim.get('text')):
            claims.append({'text': _text(claim['text'], 2000), 'evidence_ids': _strings(claim.get('evidence_ids'), 12, 160)})
    return {'action': value['action'], 'reason': _text(value.get('reason'), 500),
            'plan': _strings(value.get('plan'), 10, 500), 'open_questions': _strings(value.get('open_questions'), 12, 500),
            'args': {'query': _text(value.get('query'), 1000), 'block_ids': _strings(value.get('block_ids'), 12, 160),
                     'object_id': _text(value.get('object_id'), 160)},
            'claims': claims[:12], 'limitations': _strings(value.get('limitations'), 12, 700)}


def _step_update(run_id, token, index, **values):
    def update(run):
        step = next(s for s in run['steps'] if s['index'] == index)
        step.update(values)
    return _change(run_id, token, update)


def _stop(run_id, token, status, reason, message):
    def stop(run):
        chosen, stop_reason, notice = status, reason, message
        if run.get('cancel_requested'):
            chosen, stop_reason, notice = 'cancelled', 'cancelled', '已取消，已有证据和进度保留。'
        elif run.get('pause_requested'):
            chosen, stop_reason, notice = 'paused', 'paused', '已暂停，已有证据和进度保留。'
        run.update(status=chosen, stop_reason=stop_reason, error=notice, pause_requested=False, cancel_requested=False)
    return _change(run_id, token, stop)


def _finish(run_id, token, decision, index):
    run = _checkpoint(run_id, token)
    doc = store.get(run['doc_id'])
    registry = {item['id']: item for item in run['evidence'] if _evidence(doc, item)}
    proposed = decision['claims']
    invalid = [n for n, claim in enumerate(proposed) if not claim['evidence_ids'] or any(e not in registry for e in claim['evidence_ids'])]
    if not proposed and decision['limitations']:
        _step_update(run_id, token, index, status='completed', finished_at=time.time(),
                     observation={'summary': '任务声明当前无法形成可靠结论，保留证据与限制。', 'evidence_ids': []})
        _change(run_id, token, lambda r: r.update(limitations=decision['limitations']))
        _stop(run_id, token, 'needs_attention', 'review_incomplete', '当前证据不足以完成目标，需要补充资料或调整目标。')
        return True
    if not registry or not proposed or invalid:
        missing = '尚无实际读取的证据。' if not registry else '结论缺少证据或引用了不存在/已失效的证据编号：' + str(invalid)
        _step_update(run_id, token, index, status='error', finished_at=time.time(),
                     observation={'summary': missing + ' 请读取资料或改写结论后再提交。', 'error': 'invalid_evidence', 'evidence_ids': []})
        _change(run_id, token, lambda r: r.update(open_questions=[missing]))
        return False
    selected = {eid for c in proposed for eid in c['evidence_ids']}
    review = _model(run_id, token, 'agent_review', {'goal': run['goal'], 'claims': proposed,
                    'evidence': [registry[eid] for eid in selected], 'limitations': decision['limitations']},
                    '独立审查研究结论。逐条检查 claims 是否被其 evidence_ids 对应证据支持，index 从 0 开始且每条恰好出现一次。'
                    '区分原文事实、视觉观察、外部资料和分析；同名作者、组织归属及因果推断不可仅凭相似猜测。'
                    '引用存在不代表结论正确。判断完整 goal 是否已经满足；缺少必要部分须 goal_met=false，在 missing 只写尚未满足目标的必要缺口，普通限制不写入 missing。'
                    '只输出 goal_met、checks[{index,supported,reason}]、missing JSON，不输出内部推理过程。')
    if not isinstance(review, dict) or not isinstance(review.get('goal_met'), bool) or not isinstance(review.get('checks'), list):
        raise provider.ProviderError('研究复核格式错误，需要再次核对。')
    checks = []
    for n in range(len(proposed)):
        candidates = [c for c in review['checks'] if isinstance(c, dict) and type(c.get('index')) is int and c['index'] == n]
        check = candidates[0] if len(candidates) == 1 else {}
        checks.append({'index': n, 'supported': check.get('supported') is True,
                       'reason': _text(check.get('reason'), 1000) or '复核未提供唯一有效判断。'})
    missing = _strings(review.get('missing'), 12, 700)
    unsupported = [c for c in checks if not c['supported']]
    missing += [c['reason'] for c in unsupported]
    clean_review = {'goal_met': review['goal_met'] and not unsupported and not missing, 'checks': checks, 'missing': missing[:12]}
    supported = [claim for claim, check in zip(proposed, checks) if check['supported']]
    complete = clean_review['goal_met'] and bool(supported)
    claim_key = lambda c: (c['text'], tuple(c['evidence_ids']))
    proposed_keys = {claim_key(c) for c in proposed}
    retained = [c for c in run['claims'] if claim_key(c) not in proposed_keys and all(eid in registry for eid in c['evidence_ids'])]
    _change(run_id, token, lambda r: r.update(claims=supported if complete else (retained + supported)[:24], review=clean_review,
                                            limitations=decision['limitations'], open_questions=missing[:12]))
    _step_update(run_id, token, index, status='completed' if complete else 'error', finished_at=time.time(),
                 observation={'summary': '结论及研究目标通过证据复核。' if complete else '复核发现缺口：' + '；'.join(missing or ['研究目标尚未完整满足。']),
                              'evidence_ids': list(selected), **({} if complete else {'error': 'review_incomplete'})})
    if complete:
        _stop(run_id, token, 'completed', 'goal_met', '')
    return complete


def run_loop(run_id, token):
    doc_id = store.agent_get(run_id)['doc_id']
    try:
        while True:
            run = _checkpoint(run_id, token)
            if run['steps_used'] >= run['max_steps'] or run['calls_used'] >= run['max_calls']:
                raise RunBudget('已达到本次研究的步数或请求上限；已保存证据和通过复核的阶段结论。')
            doc = store.get(doc_id)
            index = run['steps_used'] + 1
            def begin(current):
                current['steps_used'] = index
                current['steps'].append({'index': index, 'action': 'deciding', 'reason': '根据目标和已有证据选择下一步',
                    'args': {'query': '', 'block_ids': [], 'object_id': ''}, 'status': 'running', 'started_at': time.time(),
                    'observation': {'summary': '', 'evidence_ids': []}})
            _change(run_id, token, begin)
            try:
                raw = _model(run_id, token, 'agent_decision', {
                    'goal': run['goal'], 'tools': _tools().capabilities(doc, run['allow_web']),
                    'paper': _tools().initial_context(doc), 'plan': run['plan'], 'open_questions': run['open_questions'],
                    'observations': run['steps'][-12:], 'evidence': run['evidence'][-60:],
                    'verified_claims': run['claims'], 'steps_remaining': run['max_steps'] - index,
                    'calls_remaining': run['max_calls'] - run['calls_used'] - 1},
                    '你在执行有边界的论文研究任务。每轮依据目标、工具观察与证据，自己选择一个下一步动作；失败时可以改检索词、换工具或缩小问题。'
                    '仅可使用列出的工具；初始论文信息不是已读取证据。reason 是给用户看的简短行动目的，plan 是工作步骤，不输出内部推理。'
                    '先取得实际证据再形成结论。finish 的每个 claim 必须引用已提供 evidence 的 id，不能编造引用或把未核实信息当事实。'
                    '若复核指出缺口，要补充证据或改写主张；资源不足时以明确 limitations 结束，不能冒称目标完成。'
                    '遵守允许联网范围，不执行任意代码、下载或写入外部服务；查询针对当前论文与本任务。'
                    '只输出一个 JSON 对象，不使用 Markdown 或原生 tool_calls：'
                    '{"action":"search_paper|read_blocks|inspect_figure|web_search|finish",'
                    '"reason":"简短行动目的","plan":["工作步骤"],"open_questions":["仍缺的证据"],'
                    '"query":"本步检索词或图表问题","block_ids":["当前论文段落编号"],"object_id":"当前图表编号",'
                    '"claims":[{"text":"具体结论","evidence_ids":["已取得证据编号"]}],"limitations":["限制"]}。'
                    'action 只选一个允许值；所有字段均必填，未使用的字符串填空字符串、数组填空数组。')
                decision = _decision(raw)
                action, args = decision['action'], decision['args']
                signature = hashlib.sha256(json.dumps({'action': action, 'args': args,
                            'claims': decision['claims'] if action == 'finish' else []}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                repeats = sum(step.get('signature') == signature for step in run['steps'])
                _step_update(run_id, token, index, action=action, args=args, reason=decision['reason'], signature=signature)
                _change(run_id, token, lambda r: r.update(plan=decision['plan'], open_questions=decision['open_questions']))
                if repeats >= 2:
                    _step_update(run_id, token, index, status='error', finished_at=time.time(),
                                 observation={'summary': '相同动作已执行两次，本轮停止重复调用。', 'error': 'repeated_action', 'evidence_ids': []})
                    _stop(run_id, token, 'needs_attention', 'repeated_action', '研究反复选择相同动作，需要调整目标或补充资料。')
                    break
                if action == 'finish':
                    if _finish(run_id, token, decision, index):
                        break
                    continue
                _checkpoint(run_id, token)
                observation = _tools().execute(doc, action, args, run['allow_web'], lambda: _reserve(run_id, token))
                if not isinstance(observation, dict):
                    raise ValueError('工具未返回有效观察。')
                accepted, rejected = [], 0
                current_doc = store.get(doc_id)
                existing = {item['id']: item for item in store.agent_get(run_id)['evidence']}
                for item in observation.get('evidence', [])[:30] if isinstance(observation.get('evidence'), list) else []:
                    checked = _evidence(current_doc, item)
                    if checked and (checked['id'] not in existing or existing[checked['id']] == checked):
                        if checked['id'] not in {e['id'] for e in accepted}:
                            accepted.append(checked)
                    else:
                        rejected += 1
                def save_evidence(current):
                    known = {item['id'] for item in current['evidence']}
                    current['evidence'].extend(item for item in accepted if item['id'] not in known)
                    current['evidence'] = current['evidence'][:120]
                _change(run_id, token, save_evidence)
                summary = _text(observation.get('summary'), 3000)
                if rejected:
                    summary += f' {rejected} 条来源或引文未通过校验，未登记为证据。'
                registered = {item['id'] for item in store.agent_get(run_id)['evidence']}
                result = {'summary': summary, 'evidence_ids': [item['id'] for item in accepted if item['id'] in registered]}
                result['limitations'] = _strings(observation.get('limitations'), 8, 700)
                if observation.get('truncated'):
                    result['limitations'].append('本步仅返回部分匹配内容，不能据此声称已检查完整全文。')
                if observation.get('error'):
                    result['error'] = _text(observation['error'], 1000)
                if isinstance(observation.get('web_activity'), list):
                    result['web_activity'] = observation['web_activity'][:12]
                _step_update(run_id, token, index, status='error' if result.get('error') else 'completed',
                             finished_at=time.time(), observation=result)
                # A user stopping an in-flight request keeps its returned evidence,
                # but no subsequent planner or tool call may begin.
                _checkpoint(run_id, token)
            except (Halt, RunBudget, ProviderChanged, provider.BudgetError, provider.ConfigurationError):
                raise
            except Exception as exc:
                message = str(exc)[:1000] if isinstance(exc, (provider.ProviderError, ValueError)) else '本次工具或模型步骤未完成，可改用其他动作。'
                _step_update(run_id, token, index, status='error', finished_at=time.time(),
                             observation={'summary': message, 'error': 'step_failed', 'evidence_ids': []})
    except Halt:
        pass
    except RunBudget as exc:
        _stop(run_id, token, 'needs_attention', 'budget', str(exc))
    except ProviderChanged as exc:
        _stop(run_id, token, 'paused', 'provider_changed', str(exc))
    except provider.BudgetError as exc:
        _stop(run_id, token, 'needs_attention', 'document_budget', str(exc))
    except Exception as exc:
        _stop(run_id, token, 'error', 'runtime_error', str(exc)[:1000] if isinstance(exc, provider.ProviderError) else '研究任务遇到异常，进度已保存。')
    finally:
        try:
            def settle(current):
                for step in current['steps']:
                    if step['status'] == 'running':
                        step.update(status='interrupted', finished_at=time.time(),
                                    observation={'summary': '本步未完成，恢复时将根据已保存证据重新决定动作。', 'evidence_ids': []})
                if current['status'] == 'running':
                    current.update(status='cancelled' if current.get('cancel_requested') else 'paused',
                                   stop_reason='cancelled' if current.get('cancel_requested') else 'interrupted',
                                   pause_requested=False, cancel_requested=False)
            _change(run_id, token, settle)
        except (Halt, KeyError):
            pass
        with LOCK:
            if ACTIVE.get(doc_id) == (run_id, token):
                ACTIVE.pop(doc_id, None)


def _launch(run):
    token = uuid.uuid4().hex
    run = store.agent_change(run['id'], lambda r: r.update(execution_token=token, status='running',
                                    pause_requested=False, cancel_requested=False, error='', stop_reason='', updated_at=time.time()))
    ACTIVE[run['doc_id']] = (run['id'], token)
    try:
        threading.Thread(target=run_loop, args=(run['id'], token), daemon=True, name='paper-research-agent').start()
    except Exception:
        ACTIVE.pop(run['doc_id'], None)
        store.agent_change(run['id'], lambda r: r.update(status='error', error='研究线程无法启动，请重试。'))
        raise
    return run


def start(doc_id, goal, max_steps=8, allow_web=True):
    goal = _text(goal, 2001)
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or not 3 <= max_steps <= 16 or not 3 <= len(goal) <= 2000:
        raise HTTPException(400, '研究目标需要 3–2000 字，步数为 3–16。')
    doc = store.get(doc_id)
    if not doc.get('blocks'):
        raise HTTPException(409, '请等待论文解析完成后开始研究。')
    if not provider.configured(store.settings()):
        raise HTTPException(409, '请先连接可用的模型服务。')
    with LOCK:
        if doc_id in ACTIVE or any(r['status'] == 'running' for r in store.agent_list(doc_id)):
            raise HTTPException(409, '本篇已有正在进行的研究任务。')
        now = time.time()
        run = {'id': uuid.uuid4().hex, 'doc_id': doc_id, 'goal': goal, 'status': 'paused', 'created_at': now, 'updated_at': now,
               'allow_web': bool(allow_web), 'max_steps': max_steps, 'max_calls': max_steps * 3, 'steps_used': 0, 'calls_used': 0,
               'plan': [], 'open_questions': [], 'steps': [], 'evidence': [], 'claims': [], 'limitations': [], 'review': None,
               'error': '', 'stop_reason': '', 'pause_requested': False, 'cancel_requested': False,
               'provider_fingerprint': _fingerprint(), 'execution_token': ''}
        store.agent_insert(run)
        return _launch(run)


def control(doc_id, run_id, action='pause', max_steps=None):
    if action not in ('pause', 'resume', 'cancel'):
        raise HTTPException(400, '不支持的研究控制操作。')
    with LOCK:
        run = store.agent_get(run_id)
        if run['doc_id'] != doc_id:
            raise HTTPException(404, '本篇不存在该研究任务。')
        if action in ('pause', 'cancel'):
            if run['status'] in TERMINAL:
                return run
            busy = ACTIVE.get(doc_id, (None,))[0] == run_id
            def request(current):
                if busy:
                    current['cancel_requested' if action == 'cancel' else 'pause_requested'] = True
                else:
                    current.update(status='cancelled' if action == 'cancel' else 'paused', stop_reason=action,
                                   pause_requested=False, cancel_requested=False)
                current['updated_at'] = time.time()
            return store.agent_change(run_id, request)
        if doc_id in ACTIVE or run['status'] == 'running':
            raise HTTPException(409, '当前请求正在结束，请稍后继续。')
        if run['status'] in TERMINAL:
            raise HTTPException(409, '此任务已经结束，请新建研究任务。')
        if _fingerprint() != run['provider_fingerprint']:
            raise HTTPException(409, '模型连接已改变；恢复原连接后继续，或新建研究任务。')
        limit = max_steps if max_steps is not None else run['max_steps']
        if not isinstance(limit, int) or isinstance(limit, bool) or not 3 <= limit <= 16 or limit <= run['steps_used'] or limit * 3 <= run['calls_used']:
            raise HTTPException(400, '请提高研究步数上限（最多 16 步），再继续。')
        run = store.agent_change(run_id, lambda r: r.update(max_steps=limit, max_calls=limit * 3))
        return _launch(run)


def recover_interrupted():
    """Restart never silently resumes spending tokens on a research task."""
    for doc in store.all_docs():
        for run in store.agent_list(doc['id']):
            if run['status'] != 'running':
                continue
            def recover(current):
                for step in current.get('steps', []):
                    if step['status'] == 'running':
                        step.update(status='interrupted', finished_at=time.time(),
                                    observation={'summary': '服务重启中断了此步，请手动继续。', 'evidence_ids': []})
                cancelled = bool(current.get('cancel_requested'))
                current.update(status='cancelled' if cancelled else 'paused', stop_reason='cancelled' if cancelled else 'interrupted',
                               pause_requested=False, cancel_requested=False, execution_token='',
                               error='已取消，保留已有证据。' if cancelled else '上次研究被中断，已保留证据。请确认后继续。', updated_at=time.time())
            store.agent_change(run['id'], recover)
