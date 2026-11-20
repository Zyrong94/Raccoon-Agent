"use client";

import { createContext } from "react";

export const SidebarNavigationContext = createContext<{
  onNavigate?: (() => void) | undefined;
  navigateToChat?: (action:()=>void)=>void;
}>({});
