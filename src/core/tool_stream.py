"""Assemble native streaming tool protocols without exposing reasoning blocks."""
import json
from src.core.tool_chat import ToolProtocolError


class Assembly:
    def __init__(self,style):
        self.style=style
        self.text=''
        self.calls={}
        self.blocks={}
        self.partial={}
        self.reasoning=''
        self.finish=None
        self.response=None

    def feed(self,event):
        if event.get('error') or event.get('type') in ('error','response.failed','response.incomplete'):
            raise ToolProtocolError('模型流式响应失败或未完整结束')
        token=''
        if self.style=='chat':
            for choice in event.get('choices',[]):
                if choice.get('index',0)!=0:
                    continue
                self.finish=choice.get('finish_reason') or self.finish
                delta=choice.get('delta',{})
                token+=delta.get('content') or ''
                self.reasoning+=delta.get('reasoning_content') or ''
                for c in delta.get('tool_calls') or []:
                    value=self.calls.setdefault(c['index'],{'id':'','type':'function','function':{'name':'','arguments':''}})
                    value['id']+=c.get('id','')
                    for k in ('name','arguments'):
                        value['function'][k]+=c.get('function',{}).get(k,'')
        elif self.style=='responses':
            if event.get('type')=='response.output_text.delta':
                token=event.get('delta','')
            if event.get('type')=='response.completed':
                self.response=event.get('response')
                self.finish='completed'
        else:
            kind=event.get('type')
            index=event.get('index',0)
            if kind=='content_block_start':
                self.blocks[index]=dict(event['content_block'])
            elif kind=='content_block_delta':
                delta=event.get('delta',{})
                block=self.blocks.setdefault(index,{'type':'text','text':''})
                if delta.get('type')=='text_delta':
                    token=delta.get('text','')
                    block['text']=block.get('text','')+token
                elif delta.get('type')=='input_json_delta':
                    self.partial[index]=self.partial.get(index,'')+delta.get('partial_json','')
                elif delta.get('type')=='thinking_delta':
                    block['thinking']=block.get('thinking','')+delta.get('thinking','')
                elif delta.get('type')=='signature_delta':
                    block['signature']=block.get('signature','')+delta.get('signature','')
            elif kind=='message_delta':
                self.finish=event.get('delta',{}).get('stop_reason') or self.finish
        self.text+=token
        return token

    def wire(self):
        if not self.finish:
            import httpx
            raise httpx.RemoteProtocolError('模型流未完整结束')
        if self.style=='chat':
            return {'choices':[{'finish_reason':self.finish,'message':{'role':'assistant','content':self.text,
                'reasoning_content':self.reasoning,'tool_calls':[self.calls[k] for k in sorted(self.calls)]}}]}
        if self.style=='responses':
            if self.response is None:
                raise ToolProtocolError('Responses 流缺少完整 response 输出，不能安全继续工具会话')
            return self.response
        for index,partial in self.partial.items():
            self.blocks[index]['input']=json.loads(partial)
        return {'stop_reason':self.finish,'content':[self.blocks[k] for k in sorted(self.blocks)]}
