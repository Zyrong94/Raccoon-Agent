import type {Attachment,AttachmentAdapter,CompleteAttachment} from '@assistant-ui/react';
import type {AttachmentDescriptor} from '../workbench/types';
import type {AttachmentScope,WorkbenchStore} from '../workbench/client';

export function attachmentUrl(value:string):string|undefined{
  const allowed=(path:string)=>path.startsWith('/api/attachments/')||path.startsWith('/api/main-session/attachments/');
  if(!allowed(value)||/[\\\u0000-\u0020]/.test(value))return;
  const url=new URL(value,'http://raccoon.local');
  if(url.origin==='http://raccoon.local'&&allowed(url.pathname))return value;
}

export function toRuntimeAttachment(attachment:AttachmentDescriptor):CompleteAttachment{
  const url=attachmentUrl(attachment.kind==='image'?attachment.content_url:attachment.download_url);
  return {id:attachment.id,type:attachment.kind,name:attachment.name,contentType:attachment.content_type,status:{type:'complete'},content:!url?[]:attachment.kind==='image'
    ?[{type:'image',image:url,filename:attachment.name}]
    :[{type:'file',data:url,mimeType:attachment.content_type,filename:attachment.name,sourceType:'url'}]};
}

export function createAttachmentAdapter(store:WorkbenchStore,scope:AttachmentScope,current:()=>readonly Attachment[]):AttachmentAdapter{
  return createScopedAttachmentAdapter({
    check(){
      const state=store.get();
      if(!state.connected||state.mutating||state.snapshot?.pending)throw new Error('请在连接就绪且无待确认请求时添加附件');
      if(state.snapshot?.project.id!==scope.projectId||state.snapshot?.session.id!==scope.sessionId)throw new Error('工作区或会话已切换，请返回原会话重试');
    },
    capabilities:()=>store.composerSettings().capabilities,
    upload:file=>store.uploadAttachment(file,scope),
    reportError:store.reportError,
  },current);
}

// Personal and project conversations share selection limits, previews and upload caching.
export function createScopedAttachmentAdapter(port:{check:()=>void;capabilities:()=>{images:boolean;pdf?:boolean}|undefined;upload:(file:File)=>Promise<AttachmentDescriptor>;reportError:(error:Error)=>void},current:()=>readonly Attachment[]):AttachmentAdapter{
  const reserved=new Map<string,File>();
  const uploads=new WeakMap<File,Promise<AttachmentDescriptor>>();
  const uploadedSizes=new Map<string,number>();
  const fail=(message:string):never=>{const error=new Error(message);port.reportError(error);throw error;};
  return {
    accept:'*',
    async add({file}){
      try{port.check();}catch(error){fail((error as Error).message);}
      if(file.size>20*1024*1024)fail('每个附件最多 20 MiB');
      const files=new Map(current().map(attachment=>[attachment.id,attachment.file?.size??uploadedSizes.get(attachment.id)??0]));
      for(const [id,file] of reserved)files.set(id,file.size);
      if(files.size>=16)fail('每条消息最多 16 个附件');
      if([...files.values()].reduce((sum,size)=>sum+size,0)+file.size>40*1024*1024)fail('每条消息附件总大小最多 40 MiB');
      const id=crypto.randomUUID();reserved.set(id,file);
      const image=/^image\/(png|jpeg|gif|webp)$/.test(file.type)||(!file.type&&/\.(png|jpe?g|gif|webp)$/i.test(file.name));
      return {id,type:image?'image':'file',name:file.name,contentType:file.type,file,status:{type:'requires-action',reason:'composer-send'}};
    },
    async send(attachment){
      if(attachment.type==='image'&&port.capabilities()?.images===false)fail('当前模型不支持图片，请切换支持图片的模型后重试');
      let upload=uploads.get(attachment.file);
      if(!upload){upload=port.upload(attachment.file);uploads.set(attachment.file,upload);}
      try{
        const descriptor=await upload;
        reserved.delete(attachment.id);
        uploadedSizes.set(descriptor.id,descriptor.size);
        return {...toRuntimeAttachment(descriptor),file:attachment.file};
      }
      catch(error){uploads.delete(attachment.file);throw error;}
    },
    async remove(attachment){
      // Removing a draft never deletes the durable upload, which may already belong to history.
      reserved.delete(attachment.id);
    },
  };
}
