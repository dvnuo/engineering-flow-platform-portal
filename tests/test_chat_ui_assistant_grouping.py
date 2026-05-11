import json, shutil, subprocess
from pathlib import Path
import pytest
from _js_extract_helpers import _extract_js_function

def src(): return Path('app/static/js/chat_ui.js').read_text(encoding='utf-8')

def run(script):
    node=shutil.which('node')
    if not node: pytest.skip('node missing')
    return json.loads(subprocess.run([node,'-e',script],capture_output=True,text=True,check=True).stdout)

def test_grouping_cases():
    s=src(); f1=_extract_js_function(s,'getAssistantDisplayGroupKey'); f2=_extract_js_function(s,'groupSessionMessagesForDisplay'); f3=_extract_js_function(s,'getAssistantGroupMarkdown'); f4=_extract_js_function(s,'getAssistantGroupMessageIds')
    script=f"""{f1}\n{f2}\n{f3}\n{f4}\nconst c1=[{{id:'u1',role:'user',content:'Q'}},{{id:'a1',role:'assistant',content:'A1'}},{{id:'a2',role:'assistant',content:'A2'}}];
const g1=groupSessionMessagesForDisplay(c1);
const c2=[{{id:'u1',role:'user',content:'Q'}},{{id:'a1',role:'assistant',content:'A1'}},{{id:'a2',role:'assistant',content:'A2'}},{{id:'u2',role:'user',content:'Q2'}},{{id:'a3',role:'assistant',content:'A3'}}];
const c3=[{{id:'u1',role:'user',content:'Q'}},{{id:'a1',role:'assistant',content:'A1',request_id:'r1'}},{{id:'a2',role:'assistant',content:'A2',request_id:'r2'}}];
const c4=[{{id:'u1',role:'user',content:'Q'}},{{id:'a1',role:'assistant',content:'A1'}},{{role:'tool',content:'hidden'}},{{id:'a2',role:'assistant',content:'A2'}}];
const c5=[{{id:'a0',role:'assistant',content:'orphan'}}];
console.log(JSON.stringify({{g1Len:g1.length,g1Ids:getAssistantGroupMessageIds(g1[1]),g1Md:getAssistantGroupMarkdown(g1[1]),g2:groupSessionMessagesForDisplay(c2).filter(e=>e.type==='assistant_group').length,g3:groupSessionMessagesForDisplay(c3).filter(e=>e.type==='assistant_group').length,g4:getAssistantGroupMessageIds(groupSessionMessagesForDisplay(c4)[1]),g5:groupSessionMessagesForDisplay(c5).length}}));"""
    d=run(script)
    assert d['g1Len']==2 and d['g1Ids']==['a1','a2'] and d['g1Md']=='A1\n\nA2'
    assert d['g2']==2 and d['g3']==2 and d['g4']==['a1','a2'] and d['g5']==1
