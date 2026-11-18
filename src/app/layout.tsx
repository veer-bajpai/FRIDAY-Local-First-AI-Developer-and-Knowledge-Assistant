import type { Metadata, Viewport } from "next";
import { Geist_Mono, Inter } from "next/font/google";
import Script from "next/script";
import { THEME_BOOTSTRAP } from "./ui/theme";
import "./globals.css";

const sans = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const mono = Geist_Mono({
  variable: "--font-code",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "FRIDAY · Local AI workspace",
  description: "A local-first personal assistant for documents, memory, chat and coding tasks.",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f5f8" },
    { media: "(prefers-color-scheme: dark)", color: "#0c0d11" },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} h-full antialiased`} suppressHydrationWarning>
      <body suppressHydrationWarning className="min-h-full">
        {children}
        <Script id="theme-bootstrap" strategy="beforeInteractive">
          {THEME_BOOTSTRAP}
        </Script>
      </body>
    </html>
  );
}
