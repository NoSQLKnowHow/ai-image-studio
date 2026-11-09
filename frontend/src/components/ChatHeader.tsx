import { useEffect, useState } from "react";
import type { ChatInfo } from "../builderTypes";
import type { Project } from "../types";

/** A chat's name and project (DESIGN.md §37.2): it can be renamed and filed in a project, and (from the list) deleted. */
export function ChatHeader({ chat, projects, onChange }: { chat: ChatInfo; projects: Project[]; onChange: (fields: { title?: string; project_id?: string | null }) => Promise<string | null> }) {
  const [title, setTitle] = useState(chat.title);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => setTitle(chat.title), [chat.id, chat.title]);
  const rename = async () => {
    const next = title.trim();
    if (!next || next === chat.title) return setTitle(chat.title);
    setProblem(await onChange({ title: next }));
  };
  return (
    <header className="builder-chat-header">
      <label className="sr-only" htmlFor="builder-chat-title">Chat name</label>
      <input id="builder-chat-title" className="builder-title-input" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} onBlur={() => void rename()}
        onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
      <label>Project <select value={chat.project_id ?? ""} onChange={async (e) => setProblem(await onChange({ project_id: e.target.value || null }))} aria-label="Project of this chat">
        <option value="">No project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      {problem && <span className="form-error" role="alert">{problem}</span>}
    </header>
  );
}
