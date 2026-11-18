"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { LogoMark } from "../ui/Logo";
import "./login.css";

/** Placeholder sign-in screen. FRIDAY runs locally and does not require an account. */
export default function LoginPage() {
  return (
    <main className="login">
      <form className="login-card" onSubmit={(event) => event.preventDefault()}>
        <LogoMark size={40} />
        <h1>Welcome to FRIDAY</h1>
        <p>Your local AI workspace. Accounts are not needed when FRIDAY runs on this device.</p>
        <label>
          Email
          <input type="email" placeholder="you@example.com" autoComplete="email" />
        </label>
        <label>
          Password
          <input type="password" placeholder="Enter your password" autoComplete="current-password" />
        </label>
        <button type="submit">
          Continue <ArrowRight size={16} />
        </button>
        <small>
          Working locally? <Link href="/">Open your workspace</Link>
        </small>
      </form>
    </main>
  );
}
