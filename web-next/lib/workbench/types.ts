export type AttachmentDescriptor = {id:string;kind:'image'|'file';name:string;content_type:string;size:number;content_url:string;download_url:string};
export type RunPhase='thinking'|'responding'|'waiting_tools'|null;
export type MessageMode='steer'|'follow_up'|'auto';
export type PendingMessage={id:string;text:string;mode:MessageMode};
export type ParallelStatus='running'|'succeeded'|'failed'|'cancelled'|'interrupted';
export type ParallelReply={source_event_id:string;child_session_id:string|null;question:string;status:ParallelStatus;answer:string|null;error:string|null;created_at?:string;updated_at?:string};
export type Message = { id: string; role: 'user'|'assistant'|'tool'; content: string; reasoning?:string; attachments?:AttachmentDescriptor[]; name?: string; ok?: boolean; tool_call_id?: string; calls?: {id:string;name:string;arguments:unknown}[] };
export type Activity = {id:string;name:string;kind:string;order?:number;arguments?:unknown;content?:string;ok?:boolean|null;review?:unknown};
export type Pending = {id:string;kind:string;question:string;options:{id:string;label:string;description?:string}[];payload:Record<string,unknown>;presentation?:{title:string;target:string;description:string;details:string;options:Pending['options']}};
export type Workspace = {id:string;name:string;path:string;archived?:boolean;removed?:boolean;projectless?:boolean};
export type ConversationSummary={id:string;project_id:string;session_id:string;title:string;updated_at?:string|null;pinned:boolean;archived:boolean;busy?:boolean;pending?:boolean};
export type SidebarProject={id:string;name:string;path:string;available:boolean;conversations:ConversationSummary[];projectless?:boolean;error?:string};
export type SidebarSnapshot={projects:SidebarProject[];recent:ConversationSummary[];current_conversation_id:string|null;new_chat:boolean};
export type DirectoryListing={path:string;parent:string|null;directories:{name:string;path:string}[]};
export type ArchiveConversation={id:string;session_id:string;title:string;updated_at?:string|null;storage_project_id:string;project_id:string|null};
export type ArchiveProject={id:string;name:string;path:string;archived:boolean;available:boolean;conversations:ArchiveConversation[];error?:string};
export type ArchiveSnapshot={projects:ArchiveProject[];unassigned:ArchiveConversation[]};
export type SessionSummary = {id:string;conversation_id?:string;title:string;pinned?:boolean;updated_at?:string|null;message_count?:number;archived?:boolean;deleted?:boolean;status?:string};
export type Artifact = {id:string;name:string;size:number;suffix:string;mime:string;content_url:string;download_url:string;canvas_url?:string};
export type Canvas = {artifact_id:string;title:string;request_id:string;revision?:string};
export type PermissionMode = 'ask'|'auto'|'bypass';
export type Snapshot = {
  navigation_id?:string;
  event_seq?:number;
  event_epoch?:string;
  sidebar:SidebarSnapshot;
  history_revision?:string;
  revisable_message_ids?:string[];
  versions?:{current:string;items:{session_id:string;label:string;operation:'original'|'edit'|'regenerate'}[]};
  project:Workspace;workspaces:Workspace[];session:{id:string;title:string};
  sessions:SessionSummary[];
  model:string|null;models:{ref:string;label:string;effort?:string;supported_reasoning_efforts?:string[];
    input_capabilities?:{images:boolean;pdf:boolean}}[];effort?:string;
  input_capabilities?:{images:boolean;pdf:boolean};
  busy:boolean;cancellable:boolean;status:string;pending:Pending|null;error:string|null;permission_mode:PermissionMode;
  run_phase?:RunPhase;running_tool_call_ids?:string[];
  pending_messages?:PendingMessage[];parallel_replies?:ParallelReply[];
  messages:Message[];live:{content:string;reasoning:string;order?:number}[];activity:Activity[];
  canvas?:Canvas|null;artifacts?:Artifact[];
};
type EventScope={project_id?:string;session_id?:string};
type RunState=Pick<Snapshot,'busy'|'cancellable'|'status'|'pending'|'error'|'run_phase'|'running_tool_call_ids'|
  'pending_messages'|'parallel_replies'|'history_revision'|'revisable_message_ids'|'versions'>;
export type ServerEvent = ({kind:'ready'}|{kind:'resync';reset_cursor?:boolean}|{kind:'snapshot';state:Snapshot}
  |{kind:'message_committed';project_id:string;session_id:string;messages:Message[]}
  |{kind:'run_state';project_id:string;session_id:string;state:RunState;settled:boolean}
  |{kind:'permission_mode';project_id:string;session_id:string;permission_mode:PermissionMode}
  |{kind:'model';project_id:string;session_id:string;model:string;effort?:string;input_capabilities?:{images:boolean;pdf:boolean}}
  |{kind:'sidebar_item';project_id:string;conversation_id:string;item:ConversationSummary|null;sessions:SessionSummary[]}|({kind:'run_phase';event:{phase:RunPhase}}&EventScope)|({kind:'tool';event:Activity}&EventScope)|{kind:'artifacts';project_id:string;session_id:string;canvas:Canvas|null;artifacts:Artifact[]})&{seq?:number;epoch?:string;project_id?:string;session_id?:string};
