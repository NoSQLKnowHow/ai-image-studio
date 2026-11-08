import { FOR_LABELS, chatTitle } from "../builder";
import type { ChatListItem, PromptCard } from "../builderTypes";
import type { Project } from "../types";
import type { Library, ListView, Show } from "../useLibrary";

/** The list at the left (DESIGN.md §37.2): three views, Chats, Prompts and Preferences. Chats and prompts can be looked at as they are or as they are in the bin (criteria 245, 246);
 *  prompts can be searched (words in the title, the text and the tags), filtered by tag, star and project (each with its count here) and sorted. */
const VIEWS: { id: ListView; label: string }[] = [{ id: "chats", label: "Chats" }, { id: "prompts", label: "Prompts" }, { id: "preferences", label: "Preferences" }];

export function ViewSwitch({ view, onView }: { view: ListView; onView: (v: ListView) => void }) {
  return (
    <div className="segmented builder-list-views" role="radiogroup" aria-label="List">
      {VIEWS.map((v) => <button key={v.id} type="button" role="radio" aria-checked={view === v.id} onClick={() => onView(v.id)}>{v.label}</button>)}
    </div>
  );
}

function ShowSwitch({ show, labels, onShow }: { show: string; labels: { id: string; label: string }[]; onShow: (s: string) => void }) {
  return (
    <div className="segmented builder-show" role="radiogroup" aria-label="Show">
      {labels.map((l) => <button key={l.id} type="button" role="radio" aria-checked={show === l.id} onClick={() => onShow(l.id)}>{l.label}</button>)}
    </div>
  );
}

function ChatsList({ lib, current, onOpen, onNew, onDelete, onRestore, onForever, onEmptyBin }: {
  lib: Library; current: string | null; onOpen: (id: string) => void; onNew: () => void; onDelete: (c: ChatListItem) => void;
  onRestore: (c: ChatListItem) => void; onForever: (c: ChatListItem) => void; onEmptyBin: () => void;
}) {
  const deleted = lib.chatShow === "deleted";
  const n = lib.counts?.chats;
  return (
    <>
      <ShowSwitch show={lib.chatShow} onShow={(s) => lib.setChatShow(s as "all" | "deleted")} labels={[{ id: "all", label: n ? `All (${n.all})` : "All" }, { id: "deleted", label: n ? `Deleted (${n.deleted})` : "Deleted" }]} />
      {!deleted && <button type="button" className="button primary" onClick={onNew}>New chat</button>}
      {deleted && <button type="button" className="button small" onClick={onEmptyBin} disabled={!lib.counts || lib.counts.chats.deleted + lib.counts.prompts.deleted === 0}>Empty bin</button>}
      {lib.chats.length === 0 && <p className="hint">{deleted ? "Nothing in Deleted." : "No chats yet. A chat is saved from its first message."}</p>}
      <ul>
        {lib.chats.map((c) => (
          <li key={c.id} className={c.id === current ? "current" : undefined}>
            <button type="button" className="builder-list-open" aria-current={c.id === current || undefined} onClick={() => onOpen(c.id)}>
              <strong>{chatTitle(c.title)}</strong>
              <span className="hint">{FOR_LABELS[c.for]}{c.last_text ? ` · ${c.last_text}` : ""}</span>
            </button>
            {deleted ? (
              <>
                <button type="button" className="icon-button" aria-label={`Restore “${chatTitle(c.title)}”`} onClick={() => onRestore(c)}>↺</button>
                <button type="button" className="icon-button" aria-label={`Delete “${chatTitle(c.title)}” forever`} onClick={() => onForever(c)}>✕</button>
              </>
            ) : (
              <button type="button" className="icon-button" aria-label={`Delete “${chatTitle(c.title)}”`} onClick={() => onDelete(c)}>✕</button>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

function PromptCardView({ card, current, onOpen, onStar, projectName }: { card: PromptCard; current: boolean; onOpen: () => void; onStar: () => void; projectName: string | null }) {
  return (
    <li className={`builder-card${current ? " current" : ""}`}>
      <div className="builder-card-top">
        <button type="button" className="builder-list-open" aria-current={current || undefined} onClick={onOpen}>
          <strong>{card.title}</strong>
          <span className="hint">{FOR_LABELS[card.for]} · v{card.version}{projectName ? ` · ${projectName}` : ""}</span>
        </button>
        <button type="button" className="icon-button builder-star" aria-pressed={card.favourite} aria-label={card.favourite ? `Take the star off “${card.title}”` : `Star “${card.title}”`} onClick={onStar} disabled={!!card.deleted_at}>{card.favourite ? "★" : "☆"}</button>
      </div>
      {card.text && <p className="builder-card-text">{card.text}</p>}
      {card.tags.length > 0 && <p className="builder-card-tags">{card.tags.map((t) => <span key={t} className="badge">{t}</span>)}</p>}
      {card.thumbs.length > 0 && (
        <p className="builder-card-thumbs">{card.thumbs.map((t) => <img key={t.id} src={t.url} alt="" width={40} height={40} loading="lazy" />)}</p>
      )}
      {card.deleted_at && <p className="hint">In Deleted{card.purge_at ? `, gone for good ${card.purge_at.slice(0, 10)}` : ""}.</p>}
    </li>
  );
}

function PromptsList({ lib, projects, current, onOpen, onStar }: { lib: Library; projects: Project[]; current: string | null; onOpen: (id: string) => void; onStar: (card: PromptCard) => void }) {
  const q = lib.query;
  const n = lib.counts?.prompts;
  const set = (patch: Partial<typeof q>) => lib.setQuery({ ...q, ...patch });
  const name = (id: string | null) => (id ? projects.find((p) => p.id === id)?.name ?? null : null);
  const tags = n ? Object.entries(n.tags) : [];
  return (
    <>
      <label htmlFor="builder-search" className="sr-only">Search prompts</label>
      <input id="builder-search" type="search" className="builder-search" placeholder="Search prompts" value={q.q} onChange={(e) => set({ q: e.target.value })} />
      <ShowSwitch show={q.show} onShow={(s) => set({ show: s as Show })}
        labels={[{ id: "all", label: n ? `All (${n.all})` : "All" }, { id: "favourites", label: n ? `★ (${n.favourites})` : "★" }, { id: "deleted", label: n ? `Deleted (${n.deleted})` : "Deleted" }]} />
      <div className="builder-filters">
        <label>Project <select value={q.project} onChange={(e) => set({ project: e.target.value })}>
          <option value="">Any project</option>
          <option value="none">No project{n ? ` (${n.no_project})` : ""}</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}{n ? ` (${n.projects[p.id] ?? 0})` : ""}</option>)}
        </select></label>
        <label>Tag <select value={q.tag} onChange={(e) => set({ tag: e.target.value })}>
          <option value="">Any tag</option>
          {tags.map(([tag, count]) => <option key={tag} value={tag}>{tag} ({count})</option>)}
        </select></label>
        <label>Sort <select value={q.sort} onChange={(e) => set({ sort: e.target.value as typeof q.sort })}>
          <option value="updated">Recently changed</option><option value="newest">Newest</option><option value="title">Title</option>
        </select></label>
      </div>
      {lib.prompts.length === 0 && !lib.loading && (
        <p className="hint">{q.show === "deleted" ? "Nothing in Deleted." : q.q || q.tag || q.project || q.show === "favourites" ? "No prompt matches." : "No saved prompts yet. Save one from a chat."}</p>
      )}
      <ul aria-label="Prompts">
        {lib.prompts.map((p) => <PromptCardView key={p.id} card={p} current={p.id === current} onOpen={() => onOpen(p.id)} onStar={() => onStar(p)} projectName={name(p.project_id)} />)}
      </ul>
    </>
  );
}

export function BuilderLibrary({ lib, view, onView, projects, currentChat, currentPrompt, onOpenChat, onNewChat, onDeleteChat, onRestoreChat, onForeverChat, onEmptyBin, onOpenPrompt, onStar }: {
  lib: Library; view: ListView; onView: (v: ListView) => void; projects: Project[]; currentChat: string | null; currentPrompt: string | null;
  onOpenChat: (id: string) => void; onNewChat: () => void; onDeleteChat: (c: ChatListItem) => void; onRestoreChat: (c: ChatListItem) => void; onForeverChat: (c: ChatListItem) => void;
  onEmptyBin: () => void; onOpenPrompt: (id: string) => void; onStar: (card: PromptCard) => void;
}) {
  const prefs = lib.counts?.preferences;
  return (
    <nav className="builder-list" aria-label="Library">
      <ViewSwitch view={view} onView={onView} />
      {view === "chats" && <ChatsList lib={lib} current={currentChat} onOpen={onOpenChat} onNew={onNewChat} onDelete={onDeleteChat} onRestore={onRestoreChat} onForever={onForeverChat} onEmptyBin={onEmptyBin} />}
      {view === "prompts" && <PromptsList lib={lib} projects={projects} current={currentPrompt} onOpen={onOpenPrompt} onStar={onStar} />}
      {view === "preferences" && (
        <p className="hint">
          Short standing notes the builder is told in every reply{prefs ? `: ${prefs.enabled} on of ${prefs.all}` : ""}.
          {prefs && prefs.orphaned > 0 ? ` ${prefs.orphaned} no longer in a project.` : ""}
        </p>
      )}
      {lib.problem && <p className="form-error" role="alert">{lib.problem}</p>}
    </nav>
  );
}
