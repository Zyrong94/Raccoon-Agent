"use client";

import {createContext, useContext, useLayoutEffect, type ReactNode} from 'react';

const RowResidencyContext = createContext<((active: boolean) => void) | null>(null);

export function RowResidencyProvider({retain, children}: {retain: (active: boolean) => void; children: ReactNode}) {
  return <RowResidencyContext.Provider value={retain}>{children}</RowResidencyContext.Provider>;
}

export function useRowResidency(active: boolean): void {
  const retain = useContext(RowResidencyContext);
  useLayoutEffect(() => {
    if (!active || !retain) return;
    retain(true);
    return () => retain(false);
  }, [active, retain]);
}
