#!/usr/bin/env python3
"""Uncached asynchronous owner fetches. No client/server concurrency semaphore."""
import argparse,asyncio,csv,json,os,struct,time
from pathlib import Path
MASK64=(1<<64)-1
def deterministic_value(size,seed):
 state=seed or 0x9E3779B97F4A7C15;result=bytearray(size)
 for i in range(size):
  state^=(state<<13)&MASK64;state^=state>>7;state^=(state<<17)&MASK64;state&=MASK64;result[i]=state&255
 return bytes(result)
def fnv1a64(data):
 value=14695981039346656037
 for b in data:value=((value^b)*1099511628211)&MASK64
 return f'{value:016x}'
def load_data(path):
 result={}
 with Path(path).open() as h:
  for row in csv.DictReader(h,delimiter='\t'):
   value=deterministic_value(int(row['value_size_bytes']),int(row['content_seed']))
   if fnv1a64(value)!=row['expected_fnv1a64']:raise ValueError('seed value mismatch')
   result[row['key']]=value
 return result
def atomic(path,text):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name('.'+path.name+'.tmp');tmp.write_text(text);tmp.replace(path)
class Runtime:
 def __init__(self,node,data,control,schedule,trace,endpoint=None):
  self.node=node;self.data=data;self.control=control;self.schedule=schedule;self.trace_path=trace
  self.endpoint=endpoint or (lambda owner:(f'10.90.0.{owner}',47000+owner))
  self.clients=set();self.handlers=set();self.seen=set();self.error=None;self.activation_processed=False
 def trace(self,kind,size,index,key):
  self.trace_path.parent.mkdir(parents=True,exist_ok=True)
  with self.trace_path.open('a') as h:h.write(json.dumps(dict(ts_mono_ns=time.monotonic_ns(),node=self.node,event='pilot_wire_send',packet_type=kind,frame_bytes=size,app_request_id=index,key=key))+'\n')
 def fatal(self,error):
  self.error=repr(error);atomic(self.control/'fatal'/f'node{self.node}.json',json.dumps(dict(error=self.error,mono_ns=time.monotonic_ns())))
 async def serve(self,reader,writer):
  task=asyncio.current_task();self.handlers.add(task)
  try:
   async def exchange():
    sizes=await reader.readexactly(8);ni,nk=struct.unpack('!II',sizes)
    if ni>100 or nk>4096:raise ValueError('invalid request framing')
    index=(await reader.readexactly(ni)).decode();key=(await reader.readexactly(nk)).decode()
    value=self.data.get(key);provider=time.monotonic_ns()
    body=struct.pack('!BQQ',0 if value is not None else 1,len(value) if value is not None else 0,provider)+(value or b'')
    writer.write(body);await writer.drain();self.trace('DATA',len(body),index,key)
   await asyncio.wait_for(exchange(),5)
  except (asyncio.TimeoutError,asyncio.IncompleteReadError,ConnectionError,OSError):pass
  except Exception as e:self.fatal(e)
  finally:
   writer.close()
   try:await writer.wait_closed()
   except (ConnectionError,OSError):pass
   self.handlers.discard(task)
 async def fetch(self,f):
  index,method,key,owner,size,digest,schedule=f[:7];start=time.monotonic_ns()
  atomic(self.control/'started'/f'{index}.tsv',f'{index}\t{self.node}\t{start}\n')
  value=b'';provider=0;status='ok';writer=None
  try:
   async def exchange():
    nonlocal writer,provider
    reader,writer=await asyncio.open_connection(*self.endpoint(int(owner)))
    ib,kb=index.encode(),key.encode();body=struct.pack('!II',len(ib),len(kb))+ib+kb
    writer.write(body);await writer.drain();self.trace('REQUEST',len(body),index,key)
    rs,length,provider=struct.unpack('!BQQ',await reader.readexactly(17))
    if rs or length!=int(size):raise ValueError('provider status/size mismatch')
    return await reader.readexactly(length)
   value=await asyncio.wait_for(exchange(),5)
  except (asyncio.TimeoutError,asyncio.IncompleteReadError,ConnectionError,OSError):status='io_error'
  except Exception:status='integrity_mismatch'
  finally:
   if writer:
    writer.close()
    try:await writer.wait_closed()
    except (ConnectionError,OSError):pass
  actual=fnv1a64(value)
  if status=='ok' and (len(value)!=int(size) or actual!=digest):status='integrity_mismatch'
  end=time.monotonic_ns()
  row='\t'.join([index,str(self.node),key,status,str(len(value)),actual,str(start),str(end),'0','tcp_direct',schedule,str(provider),''])+'\n'
  target=self.control/'completions'/f'{index}.tsv'
  if target.exists():raise ValueError('duplicate application completion')
  audit=self.control/'delivery-audit'/f'node{self.node}.tsv';audit.parent.mkdir(parents=True,exist_ok=True)
  with audit.open('a') as h:h.write(row)
  atomic(target,row)
 def submit(self,f):
  if len(f)<7 or f[6]!=self.schedule or f[1]!='tcp-direct' or f[0] in self.seen:raise ValueError('invalid/duplicate command')
  self.seen.add(f[0]);task=asyncio.create_task(self.fetch(f));self.clients.add(task)
  def done(t):
   self.clients.discard(t)
   if not t.cancelled() and t.exception():self.fatal(t.exception())
  task.add_done_callback(done)
 async def activate(self):
  p=self.control/'activation'/f'node{self.node}.tsv'
  if self.node not in (1,33) or self.activation_processed or not p.exists():return
  self.activation_processed=True;f=p.read_text().strip().split('\t');peer=33 if self.node==1 else 1
  link='172.31.16.2' if self.node==1 else '172.31.16.1'
  if f!=[str(peer),f'10.90.0.{peer}',str(44000+peer),'runtime-local-bootstrap',link,self.schedule]:raise ValueError('invalid federation activation')
  async def command(*a):
   process=await asyncio.create_subprocess_exec(*a,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
   stdout,stderr=await process.communicate()
   if process.returncode:raise RuntimeError(stderr.decode())
  await command('ip','link','set','dev','eth0','up')
  for target in (range(33,65) if self.node==1 else range(1,33)):
   await command('ip','route','replace',f'10.90.0.{target}/32','via',link,'dev','eth0','src',f'10.90.0.{self.node}')
  atomic(self.control/'activated'/f'node{self.node}.tsv',f'{self.node}\t{peer}\tok\t{time.monotonic_ns()}\t\n')
 async def run(self,host='0.0.0.0',port=None):
  server=await asyncio.start_server(self.serve,host,port or 47000+self.node,backlog=128)
  atomic(self.control/'ready'/f'node{self.node}.ready',f'ready\t{self.node}\n')
  processed=set();quiet_since=time.monotonic_ns()
  try:
   while not (self.control/'stop').exists() and not self.error:
    await self.activate()
    for path in sorted((self.control/'commands'/f'node{self.node}').glob('*.tsv')):
     if path in processed:continue
     processed.add(path);self.submit(path.read_text().strip().split('\t'))
    if self.clients or self.handlers:quiet_since=time.monotonic_ns()
    if not self.clients and not self.handlers and time.monotonic_ns()-quiet_since>=100_000_000:
     for p in (self.control/'quiesce').glob('*.marker'):
      target=self.control/'quiesced'/f'{p.stem}-node{self.node}.ack'
      if not target.exists():atomic(target,f'{p.stem}\t{self.node}\t{quiet_since}\n')
    await asyncio.sleep(.002)
  except Exception as e:self.fatal(e)
  finally:
   server.close();await server.wait_closed()
   tasks=list(self.clients|self.handlers)
   for task in tasks:task.cancel()
   await asyncio.gather(*tasks,return_exceptions=True)
   atomic(self.control/'stopped'/f'node{self.node}.stopped',f'stopped\t{self.node}\n')
  if self.error:raise RuntimeError(self.error)
def main():
 p=argparse.ArgumentParser();p.add_argument('node_id',type=int);p.add_argument('data_file',type=Path);p.add_argument('control_dir',type=Path);p.add_argument('schedule_hash');p.add_argument('--trace',type=Path,required=True);a=p.parse_args()
 asyncio.run(Runtime(a.node_id,load_data(a.data_file),a.control_dir,a.schedule_hash,a.trace).run())
if __name__=='__main__':main()
