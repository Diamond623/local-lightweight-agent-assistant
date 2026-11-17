import base64
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import app
import documents


def encoded(data):
    return base64.b64encode(data).decode()


def pdf_fixture():
    stream = b'BT /F1 18 Tf 50 700 Td (Aurora PDF test) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
               b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    result = b'%PDF-1.4\n'
    offsets = [0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(result))
        result += str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref = len(result)
    result += b'xref\n0 6\n0000000000 65535 f \n'
    result += b''.join(f'{n:010d} 00000 n \n'.encode() for n in offsets[1:])
    return result+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF'


class DocumentTests(unittest.TestCase):
    def test_requested_pdf_page_does_not_return_other_pages(self):
        doc={'text':'【第 1 页】\n开头说明\n【第 2 页】\n付款期限是三十天\n【第 3 页】\n结束内容'}
        chosen=documents.excerpts(doc,'第2页说了什么')
        self.assertIn('付款期限是三十天',chosen)
        self.assertNotIn('开头说明',chosen);self.assertNotIn('结束内容',chosen)
        self.assertIn('未包含',documents.excerpts(doc,'第8页呢'))
        self.assertIn('结束内容',documents.excerpts(doc,'第2页说了什么\n那第3页呢'))

    def test_followup_inherits_topic_only_for_same_document_content(self):
        doc={'name':'合同.txt','text':'背景。'*1000+'AURORA_C51_7825 特殊协议尾部'}
        previous=[{'role':'user','attachment':json.dumps({'id':documents.identity(doc),'query':'AURORA_C51_7825是什么协议'})}]
        query=documents.retrieval_question(doc,'详细解释一下',previous)
        self.assertIn('AURORA_C51_7825',documents.excerpts(doc,query))
        replaced=dict(doc,text='完全不同的文件内容')
        self.assertEqual(documents.retrieval_question(replaced,'详细解释一下',previous),'详细解释一下')
        self.assertEqual(documents.conversation_history(replaced,previous),[])
        self.assertEqual(documents.conversation_history(doc,previous),previous)
        self.assertEqual(documents.retrieval_question(doc,'概括付款条款',previous),'概括付款条款')

    def test_document_topic_is_saved_and_reused_across_chat_turns(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app,'DATA',Path(tmp)),patch.object(app,'schedule_summary'):
            with app.database() as db:
                sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',('test',app.now())).lastrowid
            doc={'name':'test.txt','text':'普通背景。'*1000+'\nAURORA_C51_7825 特殊协议尾部','images':[]}
            response={'choices':[{'message':{'content':'测试回复'},'finish_reason':'stop'}]}
            with patch.object(app,'model_info',return_value='test'),patch.object(app,'request_json',return_value=response) as infer:
                app.chat(sid,'AURORA_C51_7825是什么协议',None,'gemma',False,doc)
                app.chat(sid,'详细解释一下',None,'gemma',False,doc)
            self.assertIn('AURORA_C51_7825',infer.call_args.args[1]['messages'][-1]['content'])

    def test_replacing_same_named_file_does_not_reuse_old_file_facts(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app,'DATA',Path(tmp)),patch.object(app,'schedule_summary'):
            with app.database() as db:
                sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',('test',app.now())).lastrowid
            old={'name':'合同.txt','text':'预算 OLD_FILE_900，签署方成都。','images':[]}
            response={'choices':[{'message':{'content':'OLD_FILE_900 成都旧文件'},'finish_reason':'stop'}]}
            with patch.object(app,'model_info',return_value='test'),patch.object(app,'request_json',return_value=response) as infer:
                app.chat(sid,'预算是多少',None,'gemma',False,old)
                app.chat(sid,'那预算呢',None,'gemma',False,dict(old,text='预算 NEW_FILE_327，签署方珠海。'))
            prompt=str(infer.call_args.args[1]['messages'])
            self.assertIn('NEW_FILE_327',prompt)
            self.assertNotIn('OLD_FILE_900',prompt)

    def test_text_and_docx(self):
        doc=documents.extract('课程.txt',encoded('指针与数组'.encode()))
        self.assertEqual(doc['text'],'指针与数组')
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            z.writestr('word/document.xml','''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>法律推理课程</w:t></w:r></w:p></w:body></w:document>''')
        doc=documents.extract('test.docx',encoded(out.getvalue()))
        self.assertIn('法律推理课程',doc['text'])

    @unittest.skipUnless(sys.platform == "darwin", "macOS PDFKit native integration")
    def test_pdf_with_native_reader(self):
        self.assertTrue((documents.ROOT/'PDFRead').is_file())
        doc=documents.extract('test.pdf',encoded(pdf_fixture()))
        self.assertIn('Aurora PDF test',doc['text'])
        self.assertEqual(doc['pages'],1)

    def test_late_document_chunk_is_retrieved(self):
        text=('普通背景文字。'*400)+'\n最终项目使用特殊协议：AURORA_C51_7825\n'
        excerpt=documents.excerpts({'text':text},'AURORA_C51_7825是什么协议')
        self.assertIn('AURORA_C51_7825',excerpt)

    def test_rejects_unsupported_document(self):
        with self.assertRaisesRegex(ValueError,'支持 PDF'):
            documents.extract('old.doc',encoded(b'oldbinary'))

    def test_gemma_receives_document_and_preserves_shared_memory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(app,'DATA',Path(tmp)):
            with app.database() as db:
                sid=db.execute('INSERT INTO sessions(title,created) VALUES(?,?)',('test',app.now())).lastrowid
                app.auto_memory.manual(db,'称呼我小林')
            calls=[]
            def infer(url,payload=None,timeout=4):
                calls.append(payload)
                return {'choices':[{'message':{'content':'已读取文档'},'finish_reason':'stop'}]}
            with patch.object(app,'model_info',return_value='gemma-4-12b'),patch.object(app,'request_json',side_effect=infer):
                app.chat(sid,'解释协议',None,'gemma',False,{'name':'test.txt','text':'协议规定使用C语言','images':[]})
            self.assertIn('小林',calls[0]['messages'][0]['content'])
            self.assertIn('协议规定使用C语言',calls[0]['messages'][-1]['content'])
            with app.database() as db:
                self.assertIn('test.txt',db.execute("SELECT content FROM messages WHERE role='user'").fetchone()[0])


if __name__=='__main__':
    unittest.main()
