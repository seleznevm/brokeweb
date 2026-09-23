import { describe,it,expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ConditionLeaf } from '../src/ConditionLeaf';
import { changeField,changeOperator,typedValue,valueError } from '../src/ruleFields';
import type { FieldCatalog,RuleField } from '../src/ruleFields';

const direction:RuleField={key:'direction',type:'enum',options:['LONG','SHORT','NONE'],operators:['==','!=','IN','NOT IN','changed','changed_to'],description:''};
const timeframe:RuleField={...direction,key:'timeframe',options:['5','30','60','D']};
const gate:RuleField={key:'metrics.gateStructure',type:'boolean',options:[true,false],operators:direction.operators,description:''};
const score:RuleField={key:'avg_setup',type:'number',operators:['>=','IN','BETWEEN','changed','crosses_above'],description:''};
const signals:RuleField={key:'signals',type:'list',options:['BRONZE','STRONG'],operators:['contains','contains_any','contains_all','changed'],description:''};
const catalog:FieldCatalog={fields:[direction,timeframe,score,signals],extra_fields:{[gate.key]:gate}};

describe('typed condition edits',()=>{
 it('keeps TF strings, booleans and numeric lists distinct',()=>{
  expect(typedValue('30, 5','enum','IN')).toEqual(['30','5']);
  expect(typedValue('65.5, 70','number','IN')).toEqual([65.5,70]);
  expect(typedValue('false','boolean','==')).toBe(false);
  expect(typedValue('123','text','==')).toBe('123');
 });
 it('changes single to multi and back without stringifying values',()=>{
  const multi=changeOperator({field:'timeframe',op:'==',value:'30'},'IN',timeframe);
  expect(multi.value).toEqual(['30']);
  expect(changeOperator(multi,'==',timeframe).value).toBe('30');
  expect(changeOperator({field:gate.key,op:'==',value:false},'IN',gate).value).toEqual([false]);
  expect(changeOperator(multi,'changed',timeframe)).not.toHaveProperty('value');
 });
 it('resets incompatible values when field type changes',()=>{
  expect(changeField({field:'avg_setup',op:'>=',value:70},'direction',direction)).toEqual({field:'direction',op:'==',value:'LONG'});
  expect(changeField({field:'direction',op:'==',value:'LONG'},gate.key,gate).value).toBe(true);
  expect(changeField({field:'avg_setup',op:'>=',value:70},'avg_setup',score).value).toBe(70);
 });
 it('validates empty multi, type mistakes and reversed bounds',()=>{
  expect(valueError({op:'IN',value:[]},timeframe)).toBeTruthy();
  expect(valueError({op:'IN',value:[30]},timeframe)).toBeTruthy();
  expect(valueError({op:'==',value:'false'},gate)).toBeTruthy();
  expect(valueError({op:'BETWEEN',value:[80,60]},score)).toBeTruthy();
  expect(valueError({op:'BETWEEN',value:[60,80]},score)).toBeNull();
 });
});

describe('condition input controls',()=>{
 const html=(field:string,op:string,value?:unknown)=>renderToStaticMarkup(<ConditionLeaf node={{field,op,value}} catalog={catalog} onChange={()=>{}} onDelete={()=>{}}/>);
 it('renders one preset selector for equality and multiselect for membership',()=>{
  expect(html('direction','==','LONG')).toContain('aria-label="Значение условия"');
  expect(html('direction','==','LONG')).not.toContain('multiple=""');
  expect(html('timeframe','IN',['30','5'])).toContain('multiple=""');
  expect(html('timeframe','IN',['30','5'])).toContain('selected=""');
 });
 it('renders list membership correctly and hides values for changed',()=>{
  expect(html('signals','contains','BRONZE')).not.toContain('multiple=""');
  expect(html('signals','contains_all',['BRONZE','STRONG'])).toContain('multiple=""');
  expect(html('direction','changed')).not.toContain('aria-label="Значение условия"');
 });
 it('renders two range controls and boolean presets for raw Pine fields',()=>{
  expect(html('avg_setup','BETWEEN',[60,80])).toContain('aria-label="Нижняя граница"');
  expect(html(gate.key,'==',false)).toContain('Нет (false)');
 });
 it('preserves unknown saved values with visible errors instead of silently replacing them',()=>{
  expect(html('direction','==','legacy')).toContain('Неизвестное значение: legacy');
  expect(html('direction','==','legacy')).toContain('role="alert"');
 });
});
