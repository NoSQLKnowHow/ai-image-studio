import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PromptBar } from "./components/PromptBar";
import { defaultOptions } from "./options";
import { CAPS } from "./testdata";

// The chip beside the Images prompt box and Help me write this (DESIGN.md §37.2, §37.3 rule 3; criteria 235, 243).

afterEach(cleanup);

const barProps = (over: Partial<ComponentProps<typeof PromptBar>> = {}): ComponentProps<typeof PromptBar> => ({
  caps: CAPS, options: defaultOptions(CAPS), prompt: "a lighthouse", submitting: false, problem: null, tray: <div data-testid="tray" />, blockReason: null, starterAvailable: false,
  onFiles: vi.fn(), onStarter: vi.fn(), onPromptFocus: vi.fn(), onPrompt: vi.fn(), onMode: vi.fn(), onScale: vi.fn(), onOpenOptions: vi.fn(), onSubmit: vi.fn(), onDraft: vi.fn(), ...over,
});
const editing = () => ({ ...defaultOptions(CAPS), mode: "edit" as const });

describe("the From Prompt builder chip", () => {
  it("is not there for words that did not come from a saved prompt", () => {
    render(<PromptBar {...barProps()} />);
    expect(document.querySelector(".from-chip")).toBeNull();
  });

  it("says which prompt and version, opens it, and can be removed", () => {
    const props = barProps({ fromPrompt: { title: "Lighthouse", version: 3 }, onOpenFrom: vi.fn(), onClearFrom: vi.fn() });
    render(<PromptBar {...props} />);
    fireEvent.click(screen.getByRole("button", { name: 'From Prompt builder: "Lighthouse" v3' }));
    expect(props.onOpenFrom).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Forget that this came from “Lighthouse”" }));
    expect(props.onClearFrom).toHaveBeenCalledTimes(1);
  });

  it("is plain text when the page cannot open the prompt, and has no ✕ when it cannot be cleared", () => {
    render(<PromptBar {...barProps({ fromPrompt: { title: "Lighthouse", version: 3 } })} />);
    expect(screen.getByText('From Prompt builder: "Lighthouse" v3').tagName).toBe("SPAN");
    expect(screen.queryByRole("button", { name: /Forget that/ })).toBeNull();
  });
});

describe("Help me write this", () => {
  it("is only in Edit, where the tray is", () => {
    render(<PromptBar {...barProps({ onHelpWrite: vi.fn() })} />);
    expect(screen.queryByRole("button", { name: "Help me write this" })).toBeNull();
    cleanup();
    render(<PromptBar {...barProps({ options: editing(), onHelpWrite: vi.fn() })} />);
    expect(screen.getByRole("button", { name: "Help me write this" })).toBeTruthy();
  });

  it("asks the page to open the builder, and is off with its reason when it cannot", () => {
    const onHelpWrite = vi.fn();
    render(<PromptBar {...barProps({ options: editing(), onHelpWrite })} />);
    fireEvent.click(screen.getByRole("button", { name: "Help me write this" }));
    expect(onHelpWrite).toHaveBeenCalledTimes(1);
    cleanup();
    const off = vi.fn();
    render(<PromptBar {...barProps({ options: editing(), onHelpWrite: off, helpWriteProblem: "The builder is not set up." })} />);
    const button = screen.getByRole("button", { name: "Help me write this" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.title).toBe("The builder is not set up.");
  });
});
