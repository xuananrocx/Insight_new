import { useState } from 'react'
import { ArrowDown, ArrowDownUp } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { accountRequest } from '@/lib/account-api'
import { Button } from '@/components/ui/button'
import { OptionSelect } from '@/components/ui/select'
import { ManagementDialog } from '@/components/management-dialog'

type Row = { user_key: string; username: string; display_name: string; provider_id: string; provider_name: string; model: string; calls: number; failures: number; last_call: number }
type Result = { items: Row[]; page: number; page_size: number; total: number; summary: { calls: number; failures: number; active_users: number }; providers: {id: string; name: string}[]; models: {model_id: string}[] }
const rate = (calls: number, failures: number) => calls ? `${((calls-failures)/calls*100).toFixed(1)}%` : '—'
const time = (value: number) => value ? new Date(value).toLocaleString() : '—'
function Pager({ data, page, setPage, size, setSize }: {data?: Result; page: number; setPage: (page: number) => void; size?: string; setSize?: (size: string) => void}) {
  const current = data?.page ?? page
  return <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground"><span>共 {data?.total ?? 0} 项 · 第 {current} 页</span><div className="flex flex-wrap items-center gap-2">{size && setSize && <OptionSelect className="w-28" aria-label="统计每页条数" value={size} onValueChange={setSize} options={['20','50'].map(value=>({value,label:`每页 ${value} 人`}))}/>}<Button size="sm" variant="outline" disabled={!data || current <= 1} onClick={() => setPage(current-1)}>上一页</Button><Button size="sm" variant="outline" disabled={!data || current*data.page_size >= data.total} onClick={() => setPage(current+1)}>下一页</Button></div></div>
}
function Breakdown({ user, params, onClose }: {user: Row; params: string; onClose: () => void}) {
  const [page,setPage] = useState(1)
  const query = new URLSearchParams(params); query.set('user_key',user.user_key); query.set('page',String(page)); query.delete('search')
  const data = useQuery({queryKey:['team-api-user-detail',query.toString()],queryFn:()=>accountRequest<Result>(`/admin/api-stats/users?${query}`)})
  return <ManagementDialog title={`${user.display_name || user.username} · 调用明细`} fixedHeight className="max-w-2xl" onClose={onClose}>
    <p className="text-xs text-muted-foreground">沿用当前时间、API、模型与测试筛选；仅展示汇总，不含提问或回答。</p>
    {data.isPending ? <p role="status">正在加载…</p> : data.error ? <p role="alert" className="text-destructive">{data.error.message}</p> : <><div className="space-y-2">{data.data.items.map(row=><div key={`${row.provider_id}:${row.model}`} className="rounded-md border p-3 text-sm"><p>{row.provider_name} · {row.model}</p><p className="mt-1 text-xs text-muted-foreground">{row.calls} 次调用 · 成功率 {rate(row.calls,row.failures)} · {row.failures} 次失败</p></div>)}{!data.data.items.length && <p>当前筛选下暂无记录</p>}</div><Pager data={data.data} page={page} setPage={setPage}/></>}
  </ManagementDialog>
}
export function TeamApiStats() {
  const [days,setDays]=useState('7'), [provider,setProvider]=useState(''), [model,setModel]=useState(''), [search,setSearch]=useState(''), [draft,setDraft]=useState(''), [sort,setSort]=useState('calls')
  const [tests,setTests]=useState(false), [page,setPage]=useState(1), [size,setSize]=useState('20'), [user,setUser]=useState<Row|null>(null)
  const params=new URLSearchParams({days,provider_id:provider,model,search,sort,include_tests:String(tests),page:String(page),page_size:size})
  const result=useQuery({queryKey:['team-api-user-stats',params.toString()],queryFn:()=>accountRequest<Result>(`/admin/api-stats/users?${params}`)})
  const change=(setter:(v:string)=>void)=>(v:string)=>{setter(v);setPage(1);setUser(null)}
  const summary=result.data?.summary
  return <div className="space-y-4">
    <p className="text-xs leading-relaxed text-muted-foreground">统计已保留的团队 API 模型调用，不等于提问轮数；一轮问答可能调用多次。日志关闭、清理或旧记录缺少归属会导致统计不完整。测试默认排除；历史误记为问答的测试无法可靠识别。已删除 API 不纳入统计。</p>
    <div className="grid gap-2 sm:grid-cols-3">
      <OptionSelect aria-label="统计时间范围" value={days} onValueChange={change(setDays)} options={['1','7','30','90'].map(value=>({value,label:`最近 ${value} 天`}))}/>
      <OptionSelect aria-label="统计 API" value={provider} onValueChange={v=>{change(setProvider)(v);setModel('')}} options={[{value:'',label:'全部团队 API'},...(result.data?.providers??[]).map(p=>({value:p.id,label:p.name}))]}/>
      <OptionSelect aria-label="统计模型" value={model} onValueChange={change(setModel)} options={[{value:'',label:'全部模型'},...(result.data?.models??[]).map(m=>({value:m.model_id,label:m.model_id}))]}/>
    </div>
    <form className="flex flex-wrap items-center gap-2" onSubmit={e=>{e.preventDefault();change(setSearch)(draft)}}><input aria-label="搜索统计用户" value={draft} maxLength={200} onChange={e=>setDraft(e.target.value)} placeholder="用户名或显示名称" className="min-w-0 flex-1 rounded-md border bg-background px-3 py-2 text-sm"/><Button type="submit" variant="outline">搜索</Button><label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={tests} onChange={e=>{setTests(e.target.checked);setPage(1);setUser(null)}}/>包含测试</label></form>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[['调用次数',summary?.calls],['成功率',summary?rate(summary.calls,summary.failures):undefined],['失败次数',summary?.failures],['活跃用户',summary?.active_users]].map(([label,value])=><div key={label} className="rounded-lg border p-3"><p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 text-xl font-medium">{value??'—'}</p></div>)}</div>

    {result.isPending ? <p role="status">正在加载统计…</p> : result.error ? <p role="alert" className="text-destructive">{result.error.message}</p> : <><div className="overflow-x-auto rounded-lg border"><table className="w-full whitespace-nowrap text-left text-sm"><thead className="bg-muted/40 text-xs text-muted-foreground"><tr>{[{label:'用户'}, {label:'调用次数',key:'calls'}, {label:'成功率'}, {label:'失败次数',key:'failures'}, {label:'最近调用',key:'recent'}].map(column=><th key={column.label} scope="col" aria-sort={column.key ? sort === column.key ? 'descending' : 'none' : undefined} className="p-3 font-medium">{column.key ? <button type="button" className={`inline-flex items-center gap-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring hover:text-foreground ${sort === column.key ? 'text-foreground' : ''}`} aria-label={`按${column.label}${column.key === 'recent' ? '从新到旧' : '从多到少'}排序`} onClick={()=>change(setSort)(column.key!)}>{column.label}{sort === column.key ? <ArrowDown aria-hidden="true" className="h-3 w-3"/> : <ArrowDownUp aria-hidden="true" className="h-3 w-3 opacity-50"/>}</button> : column.label}</th>)}</tr></thead><tbody>{result.data.items.map(row=><tr key={row.user_key} className="border-t hover:bg-muted/20"><td className="p-3"><button className="text-primary hover:underline" onClick={()=>setUser(row)}>{row.display_name||row.username}</button>{row.display_name&&<p className="text-xs text-muted-foreground">{row.username}</p>}</td><td className="p-3 tabular-nums">{row.calls}</td><td className="p-3 tabular-nums">{rate(row.calls,row.failures)}</td><td className="p-3 tabular-nums">{row.failures}</td><td className="p-3 text-xs">{time(row.last_call)}</td></tr>)}</tbody></table>{!result.data.items.length&&<p className="p-6 text-center text-sm text-muted-foreground">当前筛选下暂无调用记录</p>}</div><Pager data={result.data} page={page} setPage={setPage} size={size} setSize={change(setSize)}/></>}
    {user&&<Breakdown key={`${user.user_key}:${params}`} user={user} params={params.toString()} onClose={()=>setUser(null)}/>}
  </div>
}
