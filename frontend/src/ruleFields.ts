import type { Condition } from './types';

export type FieldType='number'|'integer'|'boolean'|'text'|'enum'|'list';
export type Preset=string|number|boolean;
export interface RuleField {key:string;type:FieldType;operators:string[];options?:Preset[];description:string}
export interface FieldCatalog {fields:RuleField[];extra_fields:Record<string,RuleField>}
export const customOperators=['==','!=','>','>=','<','<=','IN','NOT IN','BETWEEN','contains','contains_any','contains_all','changed','changed_to','crosses_above','crosses_below'];
export const typeLabels:Record<FieldType,string>={number:'Число',integer:'Целое число',boolean:'Да / Нет (boolean)',text:'Текст',enum:'Готовое значение',list:'Список'};
export const multiValue=(op:string)=>['IN','NOT IN','contains_any','contains_all'].includes(op);
export function fieldSpec(catalog:FieldCatalog,key?:string){return catalog.fields.find(f=>f.key===key)??catalog.extra_fields[key??''];}
export function scalarValue(raw:string,type:FieldType):unknown {
 if(type==='text'||type==='enum'||type==='list')return raw;
 if(type==='boolean')return raw==='true'?true:raw==='false'?false:raw;
 return raw.trim()!==''&&Number.isFinite(Number(raw))?Number(raw):raw;
}
export function typedValue(raw:string,type:FieldType,op:string):unknown {
 if(!multiValue(op))return scalarValue(raw,type);
 let items:unknown[];
 try {const parsed:unknown=JSON.parse(raw);items=Array.isArray(parsed)?parsed:raw.split(',').map(v=>v.trim());}
 catch {items=raw.split(',').map(v=>v.trim());}
 return items.map(v=>typeof v==='string'?scalarValue(v,type):v);
}
export function changeOperator(node:Condition,op:string,spec:RuleField):Condition {
 if(op==='changed')return {field:node.field,op};
 const values=Array.isArray(node.value)?node.value:[node.value];
 const fallback=spec.options?.[0]??(spec.type==='number'||spec.type==='integer'?0:'');
 const first=values[0]??fallback;
 return {field:node.field,op,value:op==='BETWEEN'?[first,values[1]??first]:multiValue(op)?values.filter(v=>v!==undefined).length?values:[fallback]:first};
}
export function changeField(node:Condition,key:string,spec:RuleField):Condition {
 const op=spec.operators.includes(node.op)?node.op:spec.operators[0];
 const valid=(v:unknown)=>spec.options?spec.options.includes(v as Preset):spec.type==='number'||spec.type==='integer'?typeof v==='number':spec.type==='boolean'?typeof v==='boolean':typeof v==='string';
 const old=Array.isArray(node.value)?node.value:[node.value];
 const value=old.every(valid)?node.value:spec.options?.[0]??(spec.type==='number'||spec.type==='integer'?0:'');
 return changeOperator({field:key,op,value},op,spec);
}
export function valueError(node:Condition,spec:RuleField):string|null {
 if(!spec.operators.includes(node.op))return 'Выберите совместимый оператор.';
 if(node.op==='changed')return null;
 const multiple=multiValue(node.op)||node.op==='BETWEEN';
 if(multiple&&!Array.isArray(node.value))return 'Нужен список значений.';
 const values=multiple?node.value as unknown[]:[node.value];
 if(!values.length)return 'Выберите хотя бы одно значение.';
 if(node.op==='BETWEEN'&&values.length!==2)return 'Нужны две границы.';
 for(const v of values){
  if(spec.options&&!spec.options.includes(v as Preset))return 'Выберите значение из списка.';
  if((spec.type==='number'||spec.type==='integer')&&(typeof v!=='number'||!Number.isFinite(v)))return 'Введите число без кавычек и процентов; дробная часть через точку.';
  if(spec.type==='integer'&&typeof v==='number'&&!Number.isInteger(v))return 'Нужно целое число.';
  if(spec.type==='boolean'&&typeof v!=='boolean')return 'Выберите Да или Нет.';
  if(['text','enum','list'].includes(spec.type)&&typeof v!=='string')return 'Нужен текст.';
 }
 if(node.op==='BETWEEN'&&Number(values[0])>Number(values[1]))return 'Нижняя граница больше верхней.';
 return null;
}
