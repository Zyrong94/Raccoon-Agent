import type {Pending} from './workbench/types';

export type ScheduleDefinition={title:string;prompt:string;kind:'once'|'interval'|'cron';spec:Record<string,unknown>;timezone:string;model:string|null;permission_mode:'ask'|'auto'|'bypass'|null};
export type ScheduleExecution='main'|'project'|'isolated';
export type ScheduleRun={id:string;session_id:string;status:string;scheduled_at:number;error:string;pending:Pending|null;pending_token:string|null;execution?:ScheduleExecution;can_open_session?:boolean};
export type ScheduledTask=ScheduleDefinition&{id:string;revision:number;status:string;next_at:number|null;channel:string;origin_session:string;latest:ScheduleRun|null;execution?:ScheduleExecution;source?:{kind:'main'|'project';project_id?:string;label:string}};
export type ScheduleListing={tasks:ScheduledTask[];host:{owner:boolean;error:string;message:string}};
export type ScheduleDraft={title:string;prompt:string;preset:string;time:string;weekday:string;minutes:string;at:string;cron:string;timezone:string;model:string;permission_mode:'inherit'|'ask'|'auto'|'bypass'};

export const scheduleStatuses:Record<string,string>={enabled:'已启用',paused:'已暂停',completed:'已结束',queued:'排队中',running:'执行中',waiting:'等待确认',succeeded:'已完成',failed:'执行失败',uncertain:'结果待检查',reviewed:'已检查',skipped:'已跳过',cancelled:'已取消'};
export const weekdays=['日','一','二','三','四','五','六'];

export function blankSchedule():ScheduleDraft{
 return {title:'',prompt:'',preset:'daily',time:'09:00',weekday:'1',minutes:'120',at:'',cron:'0 9 * * *',timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC',model:'',permission_mode:'inherit'};
}

export function scheduleDefinition(draft:ScheduleDraft):ScheduleDefinition{
 if(!draft.title.trim()||!draft.prompt.trim())throw new Error('请填写任务名称和执行指令');
 const timezone=draft.timezone.trim();
 try{new Intl.DateTimeFormat('zh-CN',{timeZone:timezone}).format();}catch{throw new Error('请填写有效的时区');}
 let kind:ScheduleDefinition['kind']='cron';let spec:Record<string,unknown>;
 if(draft.preset==='once'){
  if(!draft.at||!Number.isFinite(Date.parse(draft.at)))throw new Error('请选择有效的执行时间');
  kind='once';spec={at:draft.at};
 }else if(draft.preset==='interval'){
  const seconds=Number(draft.minutes)*60;
  if(!Number.isFinite(seconds)||Math.abs(seconds-Math.round(seconds))>1e-8||seconds<60||seconds>30*86400)throw new Error('执行间隔必须在 1 分钟到 30 天之间，且精确到秒');
  kind='interval';spec={seconds:Math.round(seconds)};
 }else if(draft.preset==='custom'){
  if(!draft.cron.trim())throw new Error('请填写 Cron 表达式');
  spec={cron:draft.cron.trim()};
 }else{
  if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.time))throw new Error('请选择有效时间');
  if(draft.preset==='weekly'&&!/^[0-6]$/.test(draft.weekday))throw new Error('请选择有效的星期');
  const [hour,minute]=draft.time.split(':').map(Number);
  spec={cron:`${minute} ${hour} * * ${draft.preset==='weekly'?draft.weekday:'*'}`};
 }
 return {title:draft.title.trim(),prompt:draft.prompt.trim(),kind,spec,timezone,model:draft.model.trim()||null,permission_mode:draft.permission_mode==='inherit'?null:draft.permission_mode};
}

export function scheduleDraft(task:ScheduleDefinition):ScheduleDraft{
 const draft:ScheduleDraft={...blankSchedule(),title:task.title,prompt:task.prompt,timezone:task.timezone,model:task.model??'',permission_mode:task.permission_mode??'inherit'};
 if(task.kind==='once'){
  draft.preset='once';const value=String(task.spec.at);
  if(/(?:Z|[+-]\d\d:\d\d)$/.test(value)){
   const parts=new Intl.DateTimeFormat('en-CA',{timeZone:task.timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value));
   const get=(name:string)=>parts.find(p=>p.type===name)?.value;
   const fraction=/\.(\d+)(?:Z|[+-]\d\d:\d\d)$/.exec(value)?.[1];
   draft.at=`${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}:${get('second')}${fraction?'.'+fraction:''}`;
  }else draft.at=value;
 }else if(task.kind==='interval'){
  draft.preset='interval';draft.minutes=String(Number(task.spec.seconds)/60);
 }else{
  draft.cron=String(task.spec.cron);
  const match=/^(\d+) (\d+) \* \* (\*|[0-6])$/.exec(draft.cron);
  if(match){draft.preset=match[3]==='*'?'daily':'weekly';draft.time=`${match[2].padStart(2,'0')}:${match[1].padStart(2,'0')}`;draft.weekday=match[3]==='*'?'1':match[3];}
  else draft.preset='custom';
 }
 return draft;
}

export function scheduleChanges(draft:ScheduleDraft,original:ScheduleDefinition){
 const value=scheduleDefinition(draft),before=scheduleDefinition(scheduleDraft(original));
 const changes:Record<string,unknown>={};
 for(const field of ['title','prompt','timezone','model','permission_mode'] as const)if(value[field]!==original[field])changes[field]=value[field];
 if(value.kind!==before.kind||JSON.stringify(value.spec)!==JSON.stringify(before.spec)){changes.kind=value.kind;changes.spec=value.spec;}
 return changes;
}

export function scheduleTime(value:number|null,timezone:string){
 if(value===null)return '—';
 return new Intl.DateTimeFormat('zh-CN',{timeZone:timezone,month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value*1000));
}

export function scheduleLabel(task:ScheduleDefinition){
 if(task.kind==='once')return '仅一次';
 if(task.kind==='interval')return `每 ${Number(task.spec.seconds)/60} 分钟`;
 const draft=scheduleDraft(task);
 if(draft.preset==='daily')return `每天 ${draft.time}`;
 if(draft.preset==='weekly')return `每周${weekdays[Number(draft.weekday)]} ${draft.time}`;
 return `Cron · ${task.spec.cron}`;
}
