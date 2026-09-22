import os, json, time
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
import torch
from sentence_transformers import SentenceTransformer
from pathlib import Path
import argparse
parser=argparse.ArgumentParser()
parser.add_argument("--model-path", required=True, help="Existing local model snapshot; no download")
args=parser.parse_args()
model=SentenceTransformer(args.model_path, device='cpu', local_files_only=True)
texts=[(('安装 ADT 失败时检查版本、依赖、环境变量与日志。API 返回数据包含时间戳、字段类型及回调间隔。'+str(i))*14)[:700] for i in range(16)]
default=torch.get_num_threads()
print(json.dumps({'default_threads':default,'texts':len(texts),'chars':len(texts[0])}),flush=True)
results=[]
for threads,batch in [(default,16),(4,8),(8,8),(4,16)]:
 torch.set_num_threads(threads)
 model.encode(texts[:1],batch_size=1,show_progress_bar=False)
 start=time.perf_counter()
 vectors=model.encode(texts,batch_size=batch,show_progress_bar=False,normalize_embeddings=True)
 row={'threads':threads,'batch_size':batch,'seconds':round(time.perf_counter()-start,3),'shape':list(vectors.shape)}
 print(json.dumps(row),flush=True);results.append(row)
print(json.dumps({'results':results},indent=2))
