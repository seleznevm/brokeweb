import { useEffect, useRef, useState } from 'react';
import { columns } from './model';
import type { Condition } from './types';
import { changeField, changeOperator, customOperators, fieldSpec, multiValue, scalarValue, typedValue, typeLabels, valueError } from './ruleFields';
import type { FieldCatalog, FieldType, RuleField } from './ruleFields';

const label=(key:string)=>columns.find(c=>c[0]===key)?.[1]??key;
const display=(value:unknown)=>typeof value==='boolean'?value?'Да (true)':'Нет (false)':String(value);

function ListValue({node,spec,onChange}:{node:Condition;spec:RuleField;onChange:(node:Condition)=>void}){
 const serialize=(v:unknown)=>Array.isArray(v)?JSON.stringify(v):String(v??'');
 const [raw,setRaw]=useState(()=>serialize(node.value));
 const emitted=useRef(JSON.stringify(node.value));
 useEffect(()=>{const next=JSON.stringify(node.value);if(next!==emitted.current){emitted.current=next;setRaw(serialize(node.value));}},[node.value]);
 return <input required aria-label="Список значений" placeholder="Значения через запятую или JSON-массив" value={raw} onChange={e=>{const text=e.target.value;setRaw(text);const value=typedValue(text,spec.type,node.op);emitted.current=JSON.stringify(value);onChange({...node,value});}}/>;
}

function SingleValue({value,spec,onChange,ariaLabel='Значение условия'}:{value:unknown;spec:RuleField;onChange:(value:unknown)=>void;ariaLabel?:string}){
 if(spec.options){
  const unknown=!spec.options.some(v=>v===value);
  return <select required aria-label={ariaLabel} value={JSON.stringify(value)??''} onChange={e=>onChange(JSON.parse(e.target.value))}>
   {unknown&&<option value={JSON.stringify(value)??''}>{value===undefined?'Выберите значение':`Неизвестное значение: ${display(value)}`}</option>}
   {spec.options.map(v=><option key={JSON.stringify(v)} value={JSON.stringify(v)}>{display(v)}</option>)}
  </select>;
 }
 const numeric=spec.type==='number'||spec.type==='integer';
 return <input required aria-label={ariaLabel} type={numeric?'number':'text'} step={spec.type==='integer'?1:'any'} value={String(value??'')} placeholder={numeric?'Число, например 65.5':'Текст без кавычек'} onChange={e=>onChange(scalarValue(e.target.value,spec.type))}/>;
}

export function ConditionLeaf({node,catalog,onChange,onDelete}:{node:Condition;catalog:FieldCatalog;onChange:(value:Condition)=>void;onDelete:()=>void}){
 const initial=Array.isArray(node.value)?node.value[0]:node.value;
 const [customType,setCustomType]=useState<FieldType>(typeof initial==='number'?'number':typeof initial==='boolean'?'boolean':'text');
 const known=fieldSpec(catalog,node.field);
 const fallback:RuleField={key:node.field??'',type:customType,operators:customOperators,description:'Путь должен существовать в исходном snapshot. Тип выбран вручную.',...(customType==='boolean'?{options:[true,false]}:{})};
 const spec=known??fallback;
 const selected=Array.isArray(node.value)?node.value:[];
 const error=valueError(node,spec);
 const extra=!catalog.fields.some(f=>f.key===node.field);
 return <div className="condition-row"><div className="condition">
  <select aria-label="Метрика" value={extra?'custom':node.field} onChange={e=>{const key=e.target.value==='custom'?'metrics.':e.target.value;onChange(changeField(node,key,fieldSpec(catalog,key)??fallback));}}>
   {catalog.fields.map(f=><option key={f.key} value={f.key}>{label(f.key)} · {f.key}</option>)}<option value="custom">Исходное поле Pine / другое поле…</option>
  </select>
  {extra&&<input aria-label="Путь атрибута" value={node.field??''} placeholder="metrics.gateStructure" onChange={e=>{const key=e.target.value;const next=fieldSpec(catalog,key);onChange(next?changeField(node,key,next):{...node,field:key});}}/>}
  {!known&&<select aria-label="Тип значения" value={customType} onChange={e=>{const type=e.target.value as FieldType;setCustomType(type);onChange(changeField(node,node.field??'',{...fallback,type,options:type==='boolean'?[true,false]:undefined}));}}>{(['text','number','integer','boolean'] as const).map(t=><option key={t} value={t}>{typeLabels[t]}</option>)}</select>}
  <select aria-label="Оператор" value={node.op} onChange={e=>onChange(changeOperator(node,e.target.value,spec))}>
   {!spec.operators.includes(node.op)&&<option value={node.op}>Несовместимый: {node.op}</option>}
   {spec.operators.map(op=><option key={op} value={op}>{op==='contains_any'?'contains_any · любое из':op==='contains_all'?'contains_all · все из':op}</option>)}
  </select>
  {node.op==='BETWEEN'?<div className="condition-range"><SingleValue ariaLabel="Нижняя граница" spec={spec} value={selected[0]} onChange={v=>onChange({...node,value:[v,selected[1]]})}/><span>…</span><SingleValue ariaLabel="Верхняя граница" spec={spec} value={selected[1]} onChange={v=>onChange({...node,value:[selected[0],v]})}/></div>:
   node.op!=='changed'&&(multiValue(node.op)?spec.options?<select multiple size={Math.min(5,spec.options.length)} aria-label="Значения условия" value={selected.map(v=>JSON.stringify(v))} onChange={e=>onChange({...node,value:Array.from(e.target.selectedOptions,o=>JSON.parse(o.value))})}>
    {selected.filter(v=>!spec.options!.includes(v as string)).map(v=><option key={JSON.stringify(v)} value={JSON.stringify(v)}>Неизвестное: {display(v)}</option>)}
    {spec.options.map(v=><option key={JSON.stringify(v)} value={JSON.stringify(v)}>{display(v)}</option>)}
   </select>:<ListValue node={node} spec={spec} onChange={onChange}/>:<SingleValue spec={spec} value={node.value} onChange={value=>onChange({...node,value})}/>)}
  <button type="button" className="icon-button danger" aria-label="Удалить условие" onClick={onDelete}>×</button>
 </div><p className="condition-help">{typeLabels[spec.type]}. {spec.description} {multiValue(node.op)&&spec.options?'Несколько значений: Ctrl/⌘ + клик или Shift.':!spec.options&&spec.type==='text'?'Текст вводится без кавычек.':''}</p>{error&&<p role="alert" className="condition-error">{error}</p>}</div>;
}
