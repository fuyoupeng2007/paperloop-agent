"""Persistent reader annotations preserve geometry and original paper content."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend import app as api, layout_objects, store
from tests.test_worker_regressions import document


class Annotations(unittest.TestCase):
    def setUp(self):
        root=Path(os.environ.get('PAPERLOOP_TEST_WORK', tempfile.gettempdir()))
        root.mkdir(parents=True,exist_ok=True)
        self.directory=tempfile.TemporaryDirectory(prefix='annotations-',dir=root)
        self.addCleanup(self.directory.cleanup)
        patcher=patch.object(store,'DATA',Path(self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        store.init()
        self.doc=document() | {'fingerprint':'annotations-test','page_count':2}
        self.doc['notes']=[{'id':'legacy','text':'原有笔记','page':1,'block_id':'body'}]
        store.insert(copy.deepcopy(self.doc))

    def test_multiline_highlight_edit_and_delete_keep_originals(self):
        boxes=[[.1,.2,.8,.23],[.1,.24,.6,.27]]
        note=api.add_note('regression',api.Note(text='需要核对实验',page=1,block_id='body',
            boxes=boxes,quote='The reading method',color='blue'))
        saved=store.get('regression')['notes'][-1]
        self.assertEqual(saved['boxes'],boxes)
        self.assertEqual(saved['quote'],'The reading method')
        changed=api.edit_note('regression',note['id'],api.NoteEdit(text='已核对实验',color='green'))
        self.assertEqual(changed['text'],'已核对实验')
        for field in ('boxes','quote','block_id','page','created_at'):
            self.assertEqual(changed[field],saved[field])
        api.delete_note('regression',note['id'])
        current=store.get('regression')
        for field in ('blocks','notes','chats'):
            self.assertEqual(current[field],self.doc[field])

    def test_region_anchor_geometry_survives_reload_and_edit(self):
        box=[.2,.3,.6,.7]
        note=api.add_note('regression',api.Note(text='图内单位',page=1,box=box,anchor_id='crop-12345678'))
        self.assertEqual(store.get('regression')['notes'][-1]['visual_box'],box)
        edited=api.edit_note('regression',note['id'],api.NoteEdit(text='速度单位是 Pages/s',color='pink'))
        self.assertEqual(edited['visual_box'],box)
        self.assertEqual(edited['anchor_id'],'crop-12345678')

    def test_invalid_boxes_missing_page_and_cross_page_block_roll_back(self):
        for kwargs in (
            {'page':1,'boxes':[[.1,.2,.1,.3]]},
            {'page':1,'boxes':[[0,0,2,1]]},
            {'boxes':[[.1,.2,.3,.4]]},
            {'page':2,'block_id':'body','boxes':[[.1,.2,.3,.4]]},
            {'page':3,'boxes':[[.1,.2,.3,.4]]},
            {'page':1,'box':[.1,.2,float('nan'),.4]},
        ):
            with self.subTest(kwargs=kwargs),self.assertRaises(HTTPException):
                api.add_note('regression',api.Note(text='x',**kwargs))
        self.assertEqual(store.get('regression')['notes'],self.doc['notes'])

    def test_anchor_cannot_move_highlight_to_another_page(self):
        obj={'id':'figure-p1-example','page':1,'bbox':[.1,.2,.8,.6]}
        with patch.object(layout_objects,'get_layout',return_value={'objects':[obj]}):
            with self.assertRaises(HTTPException):
                api.add_note('regression',api.Note(text='x',page=2,anchor_id=obj['id'],boxes=[[.1,.2,.3,.4]]))
        self.assertEqual(store.get('regression')['notes'],self.doc['notes'])

    def test_empty_invalid_color_and_missing_edit_rejected(self):
        with self.assertRaises(ValidationError):
            api.Note(text='x',color='url(javascript:bad)')
        with self.assertRaises(HTTPException):
            api.add_note('regression',api.Note(text='   '))
        with self.assertRaises(HTTPException):
            api.edit_note('regression','legacy',api.NoteEdit(text='   '))
        with self.assertRaises(HTTPException) as error:
            api.edit_note('regression','missing',api.NoteEdit(text='x'))
        self.assertEqual(error.exception.status_code,404)
        self.assertEqual(store.get('regression')['notes'],self.doc['notes'])


if __name__=='__main__':
    unittest.main()
