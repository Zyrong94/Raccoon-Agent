import {expect,it} from 'vitest';
import {blankSchedule,scheduleDefinition,scheduleDraft,scheduleChanges,scheduleLabel,type ScheduleDefinition} from './scheduling';

const daily:ScheduleDefinition={title:'早间简报',prompt:'整理项目动态',kind:'cron',spec:{cron:'0 9 * * *'},timezone:'Asia/Shanghai',model:null,permission_mode:null};

it('converts daily and weekly times into existing cron rules',()=>{
 const draft={...blankSchedule(),title:'早间简报',prompt:'整理项目动态',time:'09:30',timezone:'Asia/Shanghai'};
 expect(scheduleDefinition(draft).spec).toEqual({cron:'30 9 * * *'});
 expect(scheduleDefinition({...draft,preset:'weekly',weekday:'3'}).spec).toEqual({cron:'30 9 * * 3'});
});

it('preserves timezone-aware once timestamps when only the title changes',()=>{
 const task={...daily,kind:'once' as const,spec:{at:'2026-11-06T01:12:34.123Z'}};
 expect(scheduleDraft(task).at).toBe('2026-11-06T09:12:34.123');
 expect(scheduleChanges({...scheduleDraft(task),title:'新的名称'},task)).toEqual({title:'新的名称'});
});

it('preserves custom cron expressions and non-minute intervals on unrelated edits',()=>{
 const task={...daily,spec:{cron:'*/15 8-17 * * 1-5'}};
 expect(scheduleChanges({...scheduleDraft(task),prompt:'新指令'},task)).toEqual({prompt:'新指令'});
 const interval={...daily,kind:'interval' as const,spec:{seconds:61}};
 expect(scheduleChanges({...scheduleDraft(interval),title:'新名称'},interval)).toEqual({title:'新名称'});
});

it('rejects invalid intervals, hours, weekday and timezones before saving',()=>{
 const draft={...blankSchedule(),title:'简报',prompt:'整理动态'};
 for(const minutes of ['0','-1','43201','1.001'])expect(()=>scheduleDefinition({...draft,preset:'interval',minutes})).toThrow('间隔');
 expect(()=>scheduleDefinition({...draft,time:'25:00'})).toThrow('时间');
 expect(()=>scheduleDefinition({...draft,preset:'weekly',weekday:'8'})).toThrow('星期');
 expect(()=>scheduleDefinition({...draft,timezone:'Moon/Sea'})).toThrow('时区');
});

it('describes supported rules without presenting cron as natural-language certainty',()=>{
 expect(scheduleLabel(daily)).toBe('每天 09:00');
 expect(scheduleLabel({...daily,spec:{cron:'0 9 * * 1'}})).toBe('每周一 09:00');
 expect(scheduleLabel({...daily,spec:{cron:'*/15 * * * *'}})).toBe('Cron · */15 * * * *');
});


it('defaults to session settings and supports clearing an override',()=>{
 const draft={...blankSchedule(),title:'检查',prompt:'执行检查'};
 expect(scheduleDefinition(draft)).toMatchObject({model:null,permission_mode:null});
 const configured={...daily,model:'provider/model',permission_mode:'ask' as const};
 expect(scheduleChanges({...scheduleDraft(configured),model:'',permission_mode:'inherit'},configured)).toEqual({model:null,permission_mode:null});
});
