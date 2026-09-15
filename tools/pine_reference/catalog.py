"""Stable source identifiers used by exporters, reports and documentation."""
METRICS={
 'open':('open','ohlc'),'high':('high','ohlc'),'low':('low','ohlc'),'close':('close','ohlc'),
 'atr':('atr','indicator'),'ema_fast':('emaFast','indicator'),'ema_slow':('emaSlow','indicator'),
 'return_1h':('return1h','score'),'return_6h':('return6h','score'),'return_24h':('return24h','score'),'natr_1h':('natr1h','indicator'),'volume_24h':('volume24hProxy','indicator'),
 'direction':('direction','discrete'),'generation':('lockedStartBar','discrete'),'fsm':('setupFsmState','discrete'),
 'level':('activeLevelQuality','score'),'approach':('activeApproachScore','discrete'),'formation':('formationQuality','score'),'execution':('executionQuality','score'),'geometry':('geometryQuality','score'),'context':('contextQuality','score'),'exhaustion':('exhaustionRisk','score'),'mae':('maeRisk','score'),'avg_setup':('avgSetup','score'),'continuation':('continuationScore','score'),'btc_shock':('btcShockScore','score'),
 'candidate_path':('f_pathCode(candidateEntryPath)','discrete'),'trigger_path':('f_pathCode(entryPath)','discrete'),'risk_path':('riskPathCode','discrete'),
 'sl':('displayedTradePlanSL','price'),'t1':('displayedTradePlanT1','price'),'rr':('displayedTradePlanRR','score'),
 'r1':('resistance1','price'),'r2':('resistance2','price'),'s1':('support1','price'),'s2':('support2','price'),
 'mtf_trend':('mtfTrendQualityLong','score'),'dynamic_support':('dynamicSupportQualityLong','score'),'htf_base':('htfBaseQualityLong','score'),'action':('parityActionCode','discrete'),
 'gate_liquidity':('gateLiquidity','bool'),'gate_structure':('gateStructure','bool'),'gate_trigger':('gateTrigger','bool'),'gate_btc_shock':('gateBtcShock','bool'),'fresh_trigger':('gateFreshReadyTrigger','bool'),
}
PATHS={'WAIT':0,'PRE-BREAK':1,'DIRECT MOMENTUM':2,'COMPRESSION':3,'PROTORGOVKA BREAK':4,'BREAKOUT':5,'BREAKOUT + RETEST':6,'BREAKDOWN':7,'BREAKDOWN + RETEST':8,'OPEN SKY':9}
