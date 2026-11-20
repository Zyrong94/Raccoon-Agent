"use client";
import {useEffect,useState,useSyncExternalStore} from 'react';
import {WorkbenchStore} from './client';
export function useWorkbench(){const [store]=useState(()=>new WorkbenchStore());const view=useSyncExternalStore(store.subscribe,store.get,store.get);useEffect(()=>{void store.start();return store.stop;},[store]);return {store,...view};}
