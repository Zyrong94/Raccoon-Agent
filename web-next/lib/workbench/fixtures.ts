import type {Snapshot} from './types';
export const snapshot = (patch: Partial<Snapshot> = {}): Snapshot => ({
  project: { id: 'p', name: '项目', path: '/p' }, workspaces: [], session: { id: 's', title: '会话' }, sessions: [],
  sidebar: {projects: [], recent: [], current_conversation_id: null, new_chat: true},
  model: 'm', models: [], busy: false, cancellable: false, status: 'idle', pending: null, error: null, permission_mode:'ask',
  messages: [], live: [], activity: [], ...patch,
});
