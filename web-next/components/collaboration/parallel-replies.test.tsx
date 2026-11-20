// @vitest-environment jsdom
import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it} from 'vitest';
import {ParallelReplies} from './parallel-replies';
import type {ParallelReply} from '@/lib/workbench/types';

afterEach(cleanup);

const reply=(patch:Partial<ParallelReply>={}):ParallelReply=>({source_event_id:'web-message:one',child_session_id:'child-1',question:'天台风多大',status:'running',answer:null,error:null,created_at:'2026-11-05T00:00:00Z',...patch});

it('shows the branch question, running state and later answer without adding a second card',()=>{
 const {rerender}=render(<ParallelReplies replies={[reply()]}/>);
 expect(screen.getByText('天台风多大')).toBeTruthy();
 expect(screen.getByText('正在回答')).toBeTruthy();
 rerender(<ParallelReplies replies={[reply({status:'succeeded',answer:'东风 3 级'})]}/>);
 expect(screen.getAllByText('天台风多大')).toHaveLength(1);
 expect(screen.getByText('已回答')).toBeTruthy();
 expect(screen.getByText('东风 3 级')).toBeTruthy();
 expect(screen.queryByText('正在回答')).toBeNull();
});

it('renders a failure message and stays out of the way when there is nothing to show',()=>{
 const {rerender}=render(<ParallelReplies replies={[]}/>);
 expect(screen.queryByLabelText('并行回答')).toBeNull();
 rerender(<ParallelReplies replies={[reply({status:'failed',error:'回答失败：超时'})]}/>);
 expect(screen.getByRole('alert').textContent).toContain('超时');
 expect(screen.getByText('回答失败')).toBeTruthy();
});
