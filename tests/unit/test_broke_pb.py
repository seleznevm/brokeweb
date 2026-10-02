"""Level Campaign acceptance tests. Prices are deterministic replay observations."""
import pytest
from sqlalchemy import select, func
from backend.models.repository import Repository
from backend.models.schema import LevelCampaign, CampaignTranche, CampaignOrder, BrokePBPosition, CampaignSymbolState, Delivery
from backend.engine.level_campaign import process_campaign_snapshot, targets

BASE = 1800000000000
SETTINGS = {'broke_pb_position_usdt':1000.,'campaign_exit_policy':'STRUCTURAL'}

@pytest.fixture
def repo(tmp_path):
    r=Repository(f'sqlite:///{tmp_path}/campaign.db');r.initialize();return r


def snapshot(i=0, price=100., **kw):
    s={'exchange':'BYBIT','symbol':'BTCUSDT','timeframe':'5','bar_start':BASE+i*300000,
       'event_time':BASE+i*300000, 'event_id':f'event-{i}', 'price':price,'atr':1.,
       'direction_30m':'LONG','direction_30m_confirmed':True,'ema_bias_30m':'LONG',
       'support_top':price-.2,'support_bottom':price-.5,'support_quality':75,
       'support_id':f'support-{i}', 'resistance_bottom':103.,'resistances':[103.,105.],
       'micro_quality':70,'micro_occupancy':40,'context':70,'formation':72,'execution':78,
       'approach':7,'mae':30,'exhaustion':25,'structure_valid':True,'continuation':70,
       'bar':{'start':BASE+i*300000,'open':price-.5,'high':price+.1,'low':price-.6,'close':price}}
    s.update(kw);return s


def run(repo,s,settings=None):
    with repo.session.begin() as db:
        return process_campaign_snapshot(db,s,{'campaign_exit_policy':'STRUCTURAL',**(settings or SETTINGS)},s['event_time'])


def rows(repo, model):
    with repo.session() as db:return list(db.scalars(select(model)))


def test_c1_creates_one_trade_40_percent_without_add_gates(repo):
    result=run(repo,snapshot(formation=10,execution=10,approach=1,mae=52,exhaustion=60,pm_no_add='PM_NO_ADD_MAE'))
    assert result['state']=='C1_OPEN'
    assert len(rows(repo,BrokePBPosition))==1
    t=rows(repo,CampaignTranche)[0]
    assert t.original_qty==4 and t.entry_price==100 and t.structural_sl==pytest.approx(99.2)
    assert rows(repo,CampaignOrder)[0].qty==4


def test_resistance_only_and_generic_webhook_do_not_enter(repo):
    assert run(repo,snapshot(support_top=None,support_bottom=None))['entry_block']=='NO_STRUCTURAL_ZONE'
    run(repo,snapshot(1,event='PINE READY',signal_source='tradingview'))
    assert not rows(repo,LevelCampaign)


def test_add_after_progress_and_seven_bars(repo):
    run(repo,snapshot());run(repo,snapshot(1,100.6))
    result=run(repo,snapshot(7,100.6))
    assert result['state']=='C2_OPEN'
    ts=rows(repo,CampaignTranche)
    assert ts[1].original_qty*ts[1].entry_price==pytest.approx(300)
    assert len(rows(repo,BrokePBPosition))==1


@pytest.mark.parametrize('extra,reason', [({'mae':52},'MAE'),({'pm_no_add':'PM_NO_ADD_MAE'},'MAE'),({'execution':69},'EXECUTION'),({'formation':64},'FORMATION'),({'approach':5},'APPROACH'),({'exhaustion':46},'EXHAUSTION'),({'structure_valid':False},'STRUCTURE'),({'btc_shock_blocking':True},'BTC_SHOCK')])
def test_add_hard_veto(repo,extra,reason):
    run(repo,snapshot());run(repo,snapshot(1,100.6))
    r=run(repo,snapshot(7,100.6,**extra))
    assert r['add_block']==reason
    assert len(rows(repo,CampaignTranche))==1


def test_tp1_nearest_level_half_and_be(repo):
    run(repo,snapshot(resistance_bottom=101.3,resistances=[101.3,104.]))
    t=rows(repo,CampaignTranche)[0]
    assert t.tp1==101.3 and t.runner_target==104
    run(repo,snapshot(1,101.3))
    t=rows(repo,CampaignTranche)[0]
    assert t.open_qty==2 and t.tp1_done and t.current_sl==pytest.approx(100*1.00055/.99945)
    assert rows(repo,LevelCampaign)[0].payload['realized_pnl']==pytest.approx(2.6)
    run(repo,snapshot(2,100.05))
    assert rows(repo,LevelCampaign)[0].state=='CLOSED'


def test_percentage_target_and_no_invented_runner():
    assert targets(100,'LONG',{'resistances':[105]})==(102,105)
    assert targets(100,'LONG',{})==(102,None)
    assert targets(100,'SHORT',{})==(98,None)


def test_reduce_remaining_once_per_episode(repo):
    run(repo,snapshot())
    for i in (1,2,3):run(repo,snapshot(i,100.2,mae=55,execution=54,structure_valid=False))
    assert rows(repo,CampaignTranche)[0].open_qty==2
    assert len(rows(repo,CampaignOrder))==2
    run(repo,snapshot(4,100.2,mae=30,execution=78))
    run(repo,snapshot(5,100.2,mae=55,execution=54))
    assert rows(repo,CampaignTranche)[0].open_qty==1


def test_risk_exit_has_priority_over_tp(repo):
    run(repo,snapshot())
    run(repo,snapshot(1,102,mae=81))
    c=rows(repo,LevelCampaign)[0]
    assert c.state=='CLOSED' and c.payload['close_reason']=='PM_EXIT_RISK'
    assert not rows(repo,CampaignTranche)[0].tp1_done


def test_degraded_recovery_exits_at_observed_fill(repo):
    run(repo,snapshot())
    run(repo,snapshot(1,99.65,price_only=True))
    run(repo,snapshot(2,100.01,execution=57))
    c=rows(repo,LevelCampaign)[0]
    assert c.state=='CLOSED' and c.payload['close_reason']=='EXIT_AT_BE_DEGRADED_RECOVERY'
    assert c.payload['realized_pnl']==pytest.approx(.04)


def test_confirmed_bias_flip_no_reverse(repo):
    run(repo,snapshot())
    run(repo,snapshot(6,100.2,timeframe='30',confirmed=True,direction='SHORT',ema_bias='SHORT',direction_30m_confirmed=False))
    assert rows(repo,LevelCampaign)[0].state=='CLOSED'
    assert len(rows(repo,LevelCampaign))==1


def test_duplicate_and_restart_with_c1_c2(repo):
    run(repo,snapshot());run(repo,snapshot(1,100.6));run(repo,snapshot(7,100.6))
    reopened=Repository(str(repo.engine.url))
    assert run(reopened,snapshot(7,100.6))['status']=='duplicate'
    run(reopened,snapshot(8,100.7,event='CAMPAIGN_ENTRY_1'))
    assert len(rows(repo,CampaignTranche))==2
    assert len(rows(repo,CampaignOrder))==2
    assert len(rows(repo,LevelCampaign))==1


def test_normal_reclaim_waits_next_bar_and_collapse_cancels(repo):
    s=snapshot(support_top=99.95,bar={'open':99.85,'low':99.6,'high':100.1,'close':100})
    assert run(repo,s)['entry_block']=='NO_RECLAIM'
    assert not rows(repo,LevelCampaign)
    s.update(event_id='next-tick',event_time=BASE+1000,price=100.02)
    assert run(repo,s)['entry_block']=='NO_RECLAIM'
    s.update(event_id='next-bar',event_time=BASE+300000,bar_start=BASE+300000,price=100.1)
    assert run(repo,s)['state']=='C1_OPEN'


def test_tp_aliases_do_not_double_partial(repo):
    run(repo,snapshot())
    run(repo,snapshot(1,102,event='CAMPAIGN_TP1_TAKE_AND_BE',tranche_id='C1'))
    run(repo,snapshot(2,102,event='PM_TAKE_TP1',tranche_id='C1'))
    assert rows(repo,CampaignTranche)[0].open_qty==2
    assert len(rows(repo,CampaignOrder))==2


def test_c3_and_local_stop_does_not_close_c1(repo):
    run(repo,snapshot());run(repo,snapshot(1,100.6));run(repo,snapshot(7,100.6))
    run(repo,snapshot(8,101.2));run(repo,snapshot(14,101.2,resistances=[104,106],resistance_bottom=104))
    assert rows(repo,LevelCampaign)[0].state=='C3_OPEN'
    assert len(rows(repo,CampaignTranche))==3
    run(repo,snapshot(15,100.39))
    ts=rows(repo,CampaignTranche)
    assert ts[0].open_qty==4 and ts[2].open_qty==0
    run(repo,snapshot(16,99.1))
    assert rows(repo,LevelCampaign)[0].state=='CLOSED'


def test_exhaustion_latch_and_reduction_then_tp_uses_original_qty(repo):
    run(repo,snapshot())
    run(repo,snapshot(1,100.2,exhaustion=65))
    run(repo,snapshot(2,100.2,exhaustion=70))
    assert rows(repo,CampaignTranche)[0].open_qty==2
    run(repo,snapshot(3,102))
    assert rows(repo,CampaignTranche)[0].open_qty==0
    assert rows(repo,LevelCampaign)[0].payload['realized_pnl']==pytest.approx(4.4)


def test_short_mirror(repo):
    s=snapshot(price=100,direction_30m='SHORT',ema_bias_30m='SHORT',support_top=97,support_bottom=96.8,
        resistance_bottom=100.2,resistance_top=100.5,resistance_quality=75,
        bar={'open':100.5,'high':100.6,'low':99.9,'close':100})
    assert run(repo,s)['state']=='C1_OPEN'
    t=rows(repo,CampaignTranche)[0]
    assert t.structural_sl==pytest.approx(100.8) and t.tp1==98
    s.update(event_id='short-tp',event_time=BASE+300000,bar_start=BASE+300000,price=98)
    run(repo,s)
    assert rows(repo,CampaignTranche)[0].current_sl==pytest.approx(100*.99945/1.00055)


def test_notifications_only_after_fills_in_same_transaction(repo):
    settings={**SETTINGS,'telegram_bot_token':'test','telegram_chat_id':'123'}
    run(repo,snapshot(),settings)
    assert len(rows(repo,Delivery))==1
    run(repo,snapshot(),settings)
    assert len(rows(repo,Delivery))==1


def test_no_replay_orders_no_live_simulation(repo):
    assert run(repo,snapshot(replay=True))['status']=='data_unavailable'
    assert not rows(repo,CampaignOrder)
    assert run(repo,snapshot(),{**SETTINGS,'campaign_execution_mode':'LIVE'})['entry_block']=='LIVE_EXECUTOR_UNAVAILABLE'
    assert not rows(repo,CampaignOrder)


def test_cache_confirmed_direction_and_ema_conflict(repo):
    run(repo,snapshot(timeframe='30',confirmed=False,direction='LONG',direction_30m_confirmed=False))
    assert run(repo,snapshot(1,direction_30m_confirmed=False))['entry_block']=='NO_30M_DIRECTION'
    run(repo,snapshot(6,timeframe='30',confirmed=True,direction='LONG',ema_bias='SHORT',direction_30m_confirmed=False))
    assert run(repo,snapshot(7,direction_30m_confirmed=False))['entry_block']=='30M_EMA_CONFLICT'


def test_bar_close_wait_does_not_consume_event(repo):
    s=snapshot(mode='BAR_CLOSE',confirmed=False)
    settings={**SETTINGS,'campaign_signal_mode':'BAR_CLOSE'}
    assert run(repo,s,settings)['status']=='waiting_bar_close'
    s['confirmed']=True
    assert run(repo,s,settings)['state']=='C1_OPEN'

def test_runner_and_tp_crossed_in_one_observation(repo):
    run(repo,snapshot(resistances=[101.3,102.5],resistance_bottom=101.3))
    run(repo,snapshot(1,102.6))
    t=rows(repo,CampaignTranche)[0]
    assert t.tp1_done and t.runner_done and t.open_qty==0
    assert rows(repo,LevelCampaign)[0].state=='CLOSED'


def test_failed_sweep_closes_but_btc_shock_only_vetoes_add(repo):
    run(repo,snapshot())
    run(repo,snapshot(1,100.6,btc_shock_blocking=True))
    assert rows(repo,LevelCampaign)[0].state=='C1_OPEN'
    run(repo,snapshot(2,100.6,failed_sweep_direction='LONG'))
    assert rows(repo,LevelCampaign)[0].state=='CLOSED'


def test_legacy_position_adoption_no_new_entry_order(repo):
    with repo.session.begin() as db:
        db.add(BrokePBPosition(symbol='BTCUSDT',exchange='BYBIT',timeframe='30',direction='LONG',
            status='OPEN',nominal_usdt=500,entry_price=100,entry_time=BASE,bar_start=BASE,
            sl=99,tp1=102,runner=104,payload={},updated_at=BASE))
    run(repo,snapshot(1,100.2))
    assert len(rows(repo,BrokePBPosition))==1 and not rows(repo,CampaignOrder)
    assert rows(repo,LevelCampaign)[0].payload['legacy_import']
    run(repo,snapshot(2,98.9))
    assert rows(repo,LevelCampaign)[0].state=='CLOSED'


def test_tp1_restart_restores_be_and_does_not_reuse_intrabar_low(repo):
    run(repo,snapshot())
    run(repo,snapshot(1,102,bar={'open':100,'high':102.1,'low':99.4,'close':102}))
    reopened=Repository(str(repo.engine.url))
    s=snapshot(1,101,event_id='later-tick',event_time=BASE+300001)
    s['bar']['low']=99.4
    run(reopened,s)
    t=rows(repo,CampaignTranche)[0]
    assert t.tp1_done and t.open_qty==2 and t.current_sl==pytest.approx(100*1.00055/.99945)

def test_local_five_minute_direction_cannot_override_campaign_bias(repo):
    assert run(repo,snapshot(direction='SHORT'))['state']=='C1_OPEN'
    assert rows(repo,LevelCampaign)[0].side=='LONG'
