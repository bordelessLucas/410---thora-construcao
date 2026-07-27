import React, { useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { signInWithEmail } from "../features/auth/authService";

type LocationState = {
  from?: string;
};

const isFirebaseAuthError = (value: unknown): value is { code?: unknown; message?: unknown } => {
  return typeof value === "object" && value !== null && ("code" in value || "message" in value);
};

const friendlyAuthErrorMessage = (error: unknown) => {
  if (!isFirebaseAuthError(error)) return "Não foi possível entrar. Tente novamente.";

  const code = String(error.code ?? "");
  if (code.includes("auth/invalid-credential") || code.includes("auth/wrong-password")) {
    return "E-mail ou senha inválidos.";
  }
  if (code.includes("auth/user-not-found")) {
    return "Usuário não encontrado.";
  }
  if (code.includes("auth/too-many-requests")) {
    return "Muitas tentativas. Aguarde alguns minutos e tente novamente.";
  }
  if (code.includes("auth/network-request-failed")) {
    return "Falha de rede. Verifique sua conexão e tente novamente.";
  }
  return "Não foi possível entrar. Verifique suas credenciais.";
};

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as LocationState;

  const redirectTo = useMemo(() => {
    if (typeof state.from === "string" && state.from.trim()) return state.from;
    return "/";
  }, [state.from]);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    const normalizedEmail = email.trim().toLowerCase();
    if (!normalizedEmail || !password) {
      setErrorMessage("Preencha e-mail e senha.");
      return;
    }

    setIsSubmitting(true);
    try {
      await signInWithEmail({ email: normalizedEmail, password });
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setErrorMessage(friendlyAuthErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="auth-canvas flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-md">
        <div className="mb-10 text-center">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-linear-to-br from-thora-sky to-thora-steel font-display text-2xl font-bold text-white shadow-lg shadow-thora-sky/30">
            T
          </div>
          <h1 className="font-display text-4xl font-bold tracking-tight text-white">
            Thora
          </h1>
          <p className="mt-2 text-sm text-slate-300">
            Análise inteligente de orçamentos de construção
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/95 p-8 shadow-2xl shadow-black/30 backdrop-blur-md">
          <h2 className="font-display text-2xl font-bold text-slate-900">Entrar</h2>
          <p className="mt-1 text-sm text-slate-600">
            Acesse sua conta para continuar.
          </p>

          {errorMessage && (
            <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {errorMessage}
            </div>
          )}

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700">
                E-mail
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="seuemail@empresa.com"
                autoComplete="email"
                className="input-field"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700">
                Senha
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                autoComplete="current-password"
                className="input-field"
              />
            </div>

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full rounded-xl bg-thora-ink px-6 py-3 font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? "Entrando…" : "Entrar"}
            </button>
          </form>

          <p className="mt-6 text-sm text-slate-600">
            Ainda não tem conta?{" "}
            <Link
              to="/cadastro"
              className="font-semibold text-thora-steel hover:text-thora-sky"
            >
              Criar agora
            </Link>
          </p>
        </div>

        <p className="mt-6 text-center text-xs text-slate-400">
          Ao continuar, você concorda com os termos de uso da aplicação.
        </p>
      </div>
    </div>
  );
}
