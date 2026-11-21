import type { Metadata } from "next";
import type { ReactNode } from "react";
import localFont from "next/font/local";
import './globals.css';

const publicSans=localFont({src:'./fonts/public-sans.woff2',variable:'--font-public-sans',weight:'100 900',display:'swap'});
const jetbrainsMono=localFont({src:'./fonts/jetbrains-mono.woff2',variable:'--font-jetbrains-mono',weight:'100 800',display:'swap'});

export const metadata: Metadata = { title: "Raccoon", description: "Raccoon 是在本地工作区运行的事件驱动型异步 Agent。" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="zh-CN"><body className={`${publicSans.variable} ${jetbrainsMono.variable} font-sans antialiased`}>{children}</body></html>;
}
