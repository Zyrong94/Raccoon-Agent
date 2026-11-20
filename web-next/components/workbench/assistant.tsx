"use client";
// Shared runtime and view-provider composition for the production workbench.
import type {ReactNode} from 'react';
import {useEffect} from 'react';
import {AssistantRuntimeProvider,type AssistantRuntime} from '@assistant-ui/react';
import {WorkbenchShell} from '@/components/workbench/shell';
import {WorkbenchContext} from '@/components/workbench/context';
import type {ProjectNavigation,WorkbenchConfig} from '@/components/workbench/context';
import {readStoredTheme} from '@/components/workbench/theme';
export function Assistant({runtime,runStatus,blocked,navigationBlocked,pendingConversation,chatUnavailable,headerExtra,notice,pendingSlot,modelControl,canvasState,workbench,workspace,navigation}:{runtime:AssistantRuntime;runStatus?:WorkbenchConfig['runStatus'];blocked:boolean;navigationBlocked?:boolean;pendingConversation?:WorkbenchConfig['pendingConversation'];chatUnavailable?:boolean;headerExtra:ReactNode;notice:ReactNode;pendingSlot:ReactNode;modelControl:ReactNode;canvasState?:WorkbenchConfig['canvasState'];workbench?:WorkbenchConfig['workbench'];workspace?:WorkbenchConfig['workspace'];navigation:ProjectNavigation}){
 useEffect(()=>{document.documentElement.classList.toggle('dark',readStoredTheme()==='dark');},[]);
 return <AssistantRuntimeProvider runtime={runtime}><WorkbenchContext.Provider value={{runStatus,blocked,navigationBlocked,pendingConversation,chatUnavailable,headerExtra,notice,pendingSlot,canvasState,workbench,workspace,navigation,modelControl}}><div className="h-dvh overflow-hidden" style={{'--thread-max-width':'42rem'} as import('react').CSSProperties}><WorkbenchShell navigation={navigation}/></div></WorkbenchContext.Provider></AssistantRuntimeProvider>;
}
