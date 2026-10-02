import { useState } from 'react';
import { useCampaigns, price, type Fill } from './campaigns';
import { Badge, ErrorMessage, Section } from './common';
import { useDisplayTime } from './Timezone';
import { ActiveCampaigns, CampaignHistory } from './CampaignPanels';
import { SortHeader, sortedRows, type TableSort } from './tableSort';


export function BrokePB() {
  const { timestamp } = useDisplayTime();
  const [page,setPage] = useState(0);
  const [historySort,setHistorySort] = useState<TableSort>({key:'closed_at',direction:'desc'});
  const [diagnosticSort,setDiagnosticSort] = useState<TableSort>({key:'symbol',direction:'asc'});
  const query = useCampaigns(page,historySort);
  const data = query.data;
  const diagnostics=sortedRows(data?.diagnostics??[],diagnosticSort,(d,key)=>({symbol:d.key,bias:d.campaign_bias,state:d.campaign_state,recommendation:d.recommendation??'WAIT',c1:d.last_entry_block_reason,add:d.last_add_block_reason,execution:d.last_execution?.at,counters:Object.values(d.blocked_by_reason).reduce((sum,n)=>sum+n,0)} as Record<string,unknown>)[key]);
  const execution = (fill?: Fill) => fill ? `${fill.action} · ${price(fill.qty)} @ ${price(fill.price)} · ${timestamp(fill.at)}` : 'Нет исполнений';
  return <>
    <div className="page-heading"><div>
      <p className="eyebrow">SUPPORT / RESISTANCE · LEVEL CAMPAIGN</p>
      <h1>Level Campaign <span className="count">{data?.active.length ?? 0} активных</span></h1>
      <p>Подтверждённый 30m Direction · 5m structural reclaim · До трёх траншей · Объём с учётом лимита риска</p>
      <Badge tone={data?.execution_mode === 'LIVE' ? 'warning' : 'neutral'}>{data?.execution_mode ?? 'PAPER'}</Badge>
    </div></div>
    <ErrorMessage error={query.error} />
    <ActiveCampaigns data={data} isPending={query.isPending}/>
    <Section title="Диагностика C1 / ADD">
      <p>{Object.entries(data?.counters ?? {}).map(([k, v]) => `${k}: ${v}`).join(' · ')}</p>
      <div className="table-scroll"><table className="screener sortable-table"><thead><tr>{[['symbol','Symbol'],['bias','30m'],['state','State'],['recommendation','Recommendation'],['c1','C1 blocked'],['add','ADD blocked'],['execution','Execution'],['counters','Blocked counters']].map(([column,label])=><SortHeader key={column} column={column} label={label} sort={diagnosticSort} onSort={setDiagnosticSort} title={column==='execution'?'По времени последнего исполнения':column==='counters'?'По общему числу блокировок':undefined}/>)}</tr></thead>
        <tbody>{diagnostics.map(d => <tr key={d.key}><td>{d.key}</td><td>{d.campaign_bias}</td><td>{d.campaign_state}</td><td>{d.recommendation ?? 'WAIT'}</td><td>{d.last_entry_block_reason ?? '—'}</td><td>{d.last_add_block_reason ?? '—'}</td><td>{execution(d.last_execution)}</td><td>{Object.entries(d.blocked_by_reason).map(([k,v]) => `${k}: ${v}`).join(', ')}</td></tr>)}</tbody></table></div>
    </Section>
    <CampaignHistory data={data} isPending={query.isPending} page={page} onPage={setPage} sort={historySort} onSort={sort=>{setHistorySort(sort);setPage(0);}} charts/>
  </>;
}
