import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { createAccountWithEmail } from "../features/auth/authService";

const isFirebaseAuthError = (value: unknown): value is { code?: unknown; message?: unknown } => {
  return typeof value === "object" && value !== null && ("code" in value || "message" in value);
};

const friendlyAuthErrorMessage = (error: unknown) => {
  if (!isFirebaseAuthError(error)) return "Não foi possível criar sua conta. Tente novamente.";

  const code = String(error.code ?? "");
  if (code.includes("auth/email-already-in-use")) {
    return "Esse e-mail já está em uso. Tente entrar ou recupere sua senha.";
  }
  if (code.includes("auth/invalid-email")) {
    return "E-mail inválido.";
  }
  if (code.includes("auth/weak-password")) {
    return "Senha fraca. Use pelo menos 6 caracteres.";
  }
  if (code.includes("auth/network-request-failed")) {
    return "Falha de rede. Verifique sua conexão e tente novamente.";
  }
  return "Não foi possível criar sua conta. Verifique os dados e tente novamente.";
};

export default function Cadastro() {
  const navigate = useNavigate();

  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage(null);

    const normalizedEmail = email.trim().toLowerCase();
    const name = displayName.trim();

    if (!name) {
      setErrorMessage("Informe seu nome.");
      return;
    }
    if (!normalizedEmail) {
      setErrorMessage("Informe seu e-mail.");
      return;
    }
    if (!password) {
      setErrorMessage("Informe uma senha.");
      return;
    }
    if (password !== confirmPassword) {
      setErrorMessage("As senhas não conferem.");
      return;
    }

    setIsSubmitting(true);
    try {
      await createAccountWithEmail({ email: normalizedEmail, password, displayName: name });
      navigate("/", { replace: true });
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
            Comece a analisar orçamentos com mais clareza
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/95 p-8 shadow-2xl shadow-black/30 backdrop-blur-md">
          <h2 className="font-display text-2xl font-bold text-slate-900">Criar conta</h2>
          <p className="mt-1 text-sm text-slate-600">
            Cadastre-se para salvar e acompanhar seus orçamentos.
          </p>

          {errorMessage && (
            <div className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {errorMessage}
            </div>
          )}

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700">
                Nome
              </label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Seu nome"
                autoComplete="name"
                className="input-field"
              />
            </div>

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
                placeholder="Mínimo 6 caracteres"
                autoComplete="new-password"
                className="input-field"
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-700">
                Confirmar senha
              </label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Repita a senha"
                autoComplete="new-password"
                className="input-field"
              />
            </div>

            <button
              type="submit"
              disabled={isSubmitting}
              className="w-full rounded-xl bg-thora-ink px-6 py-3 font-semibold text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? "Criando…" : "Criar conta"}
            </button>
          </form>

          <p className="mt-6 text-sm text-slate-600">
            Já tem conta?{" "}
            <Link
              to="/login"
              className="font-semibold text-thora-steel hover:text-thora-sky"
            >
              Entrar
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
