import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import type { PromptQuery } from "./builderApi";
import type { BuilderCounts, ChatListItem, Preference, PromptCard, PromptDetail } from "./builderTypes";
import type { Api } from "./useBuilder";

// The library's state (DESIGN.md §37.2, criteria 245-247): the lists of chats and prompts the person is looking at (with the search, the filters and the sort), the counts that go
// beside them, the preferences, and the prompt that is open. Each is read from the server, and read again when something changes here or on another page.

export type Show = "all" | "favourites" | "deleted";
export type ListView = "chats" | "prompts" | "preferences";

export interface LibraryQuery {
  q: string;
  tag: string;
  show: Show;
  project: string; // "" any project, "none", or a project's id
  sort: "updated" | "newest" | "title";
}

export const NO_QUERY: LibraryQuery = { q: "", tag: "", show: "all", project: "", sort: "updated" };
const SEARCH_PAUSE_MS = 250; // typing in the search box asks the server once the person pauses

const words = (error: unknown): string => (error instanceof ApiError ? error.message : error instanceof Error ? error.message : "That did not work.");

export function useLibrary(api: Api, tick: number, active: ListView) {
  const [query, setQuery] = useState<LibraryQuery>(NO_QUERY);
  const [chatShow, setChatShow] = useState<"all" | "deleted">("all");
  const [chats, setChats] = useState<ChatListItem[]>([]);
  const [prompts, setPrompts] = useState<PromptCard[]>([]);
  const [counts, setCounts] = useState<BuilderCounts | null>(null);
  const [preferences, setPreferences] = useState<Preference[]>([]);
  // each list keeps its own trouble: a list that was read fine must not wipe the words about one that was not
  const [problems, setProblems] = useState<Record<ListView, string | null>>({ chats: null, prompts: null, preferences: null });
  const setProblem = useCallback((list: ListView, words: string | null) => setProblems((all) => (all[list] === words ? all : { ...all, [list]: words })), []);
  const [detail, setDetail] = useState<PromptDetail | null>(null);
  const [detailProblem, setDetailProblem] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const wanted = useRef(query);
  wanted.current = query;
  const openId = useRef<string | null>(null);

  const refreshChats = useCallback(async () => {
    try {
      setChats((await api.listChats({ deleted: chatShow === "deleted" })).chats);
      setProblem("chats", null);
    } catch (error) {
      setProblem("chats", words(error));
    }
  }, [api, chatShow, setProblem]);

  const refreshPrompts = useCallback(async () => {
    const now = wanted.current;
    const params: PromptQuery = { q: now.q.trim() || undefined, tag: now.tag || undefined, favourite: now.show === "favourites", deleted: now.show === "deleted", project: now.project || undefined, sort: now.sort };
    setLoading(true);
    try {
      const found = (await api.listPrompts(params)).prompts;
      if (wanted.current === now) setPrompts(found); // a slower answer to an older question is not shown
      setProblem("prompts", null);
    } catch (error) {
      setProblem("prompts", words(error));
    } finally {
      setLoading(false);
    }
  }, [api, setProblem]);

  const refreshCounts = useCallback(async () => {
    try {
      setCounts(await api.counts());
    } catch {
      /* the counts are a convenience */
    }
  }, [api]);

  const refreshPreferences = useCallback(async () => {
    try {
      setPreferences((await api.listPreferences()).preferences);
      setProblem("preferences", null);
    } catch (error) {
      setProblem("preferences", words(error));
    }
  }, [api, setProblem]);

  const refreshDetail = useCallback(async () => {
    if (!openId.current) return;
    try {
      setDetail(await api.getPrompt(openId.current));
      setDetailProblem(null);
    } catch (error) {
      setDetailProblem(words(error));
    }
  }, [api]);

  const everything = useCallback(() => {
    void refreshCounts();
    void refreshChats();
    void refreshPrompts();
    void refreshPreferences();
    void refreshDetail();
  }, [refreshCounts, refreshChats, refreshPrompts, refreshPreferences, refreshDetail]);

  // a change made here or by another page: read what is on show again
  useEffect(() => {
    everything();
  }, [everything, tick]);

  // the search box: ask once the person has paused; every other change asks at once
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const timer = setTimeout(() => void refreshPrompts(), query.q.trim() ? SEARCH_PAUSE_MS : 0);
    return () => clearTimeout(timer);
  }, [query, refreshPrompts]);
  useEffect(() => void refreshChats(), [refreshChats, chatShow, active]);

  const openPrompt = useCallback(async (id: string) => {
    openId.current = id;
    setDetailProblem(null);
    try {
      setDetail(await api.getPrompt(id));
    } catch (error) {
      setDetail(null);
      setDetailProblem(words(error));
    }
  }, [api]);

  const closePrompt = useCallback(() => {
    openId.current = null;
    setDetail(null);
    setDetailProblem(null);
  }, []);

  return {
    query, setQuery, chatShow, setChatShow, chats, prompts, counts, preferences, problem: problems[active], loading, detail, detailProblem,
    refreshChats, refreshPrompts, refreshCounts, refreshPreferences, refreshDetail, everything, openPrompt, closePrompt, setDetail,
  };
}

export type Library = ReturnType<typeof useLibrary>;
