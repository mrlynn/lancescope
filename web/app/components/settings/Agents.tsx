"use client";

import { useEffect, useState } from "react";

import { Caveat, Copy, Empty, Eyebrow } from "@/app/components/console/atoms";
import { type AgentHost, type AgentsView, getAgents } from "@/app/lib/settings";

/** Connect an agent host to this console's read surface.
 *
 *  The command is worked out on the server rather than here, because only that
 *  process knows how it was started — a checkout, a wheel, the packaged app — and
 *  the whole point of this pane is that nobody has to translate a documentation
 *  page into their own paths.
 *
 *  It generates and does not install. Writing another application's config file is
 *  the one thing deliberately absent: `~/.claude.json` is Claude Code's whole state
 *  rather than a config file, `.cursor/mcp.json` is per project and this console has
 *  no idea which project, and the server's single claim about writing is that it
 *  writes its own settings file and nothing else. */
export default function Agents() {
  const [view, setView] = useState<AgentsView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pin, setPin] = useState("active");
  const [host, setHost] = useState("claude-code");

  useEffect(() => {
    let live = true;
    getAgents(pin)
      .then((v) => { if (live) { setView(v); setError(null); } })
      .catch((e) => { if (live) setError(String(e)); });
    return () => { live = false; };
  }, [pin]);

  if (error) return <section className="panel p-6"><Empty>{error}</Empty></section>;
  if (!view) return <section className="panel p-6"><Empty>reading…</Empty></section>;

  const chosen = view.hosts.find((h) => h.id === host) ?? view.hosts[0];

  return (
    <section className="panel p-6">
      <Eyebrow>Agents</Eyebrow>
      <p className="text-[13px] leading-relaxed mt-2" style={{ color: "var(--body)" }}>
        The console&rsquo;s read surface is also an MCP server, so Claude Code &mdash;
        or any agent host that speaks MCP &mdash; can inspect this database directly.
        It costs nothing to run: there is no key of ours and no model in the loop, so
        the intelligence is your agent&rsquo;s and the evidence is this
        console&rsquo;s. {view.tool_count} read-only tools, none of which can write,
        materialise a blob column, or run a query.
      </p>

      {!view.launch.runnable ? (
        <Caveat>{view.launch.detail}</Caveat>
      ) : (
        <>
          {/* Above the command rather than under it. Both of these say the block
              below is not yet the thing the reader wants, and saying that after they
              have already copied it is saying it too late. */}
          {view.launch.warning && <Caveat>{view.launch.warning}</Caveat>}
          {view.pin.mode === "none" && <Caveat>{view.pin.note}</Caveat>}

          <p className="text-[12px] mt-4" style={{ color: "var(--dim)" }}>
            {view.launch.detail}
          </p>

          <Pin view={view} pin={pin} onPin={setPin} />

          <div className="seg mt-5">
            {view.hosts.map((h) => (
              <button key={h.id} onClick={() => setHost(h.id)}
                      data-on={chosen?.id === h.id}
                      className="mono !px-3.5 text-[10px] tracking-[0.14em] uppercase">
                {h.label}
              </button>
            ))}
          </div>

          {chosen && <HostBlock host={chosen} />}

          <Caveat>
            These paths name your home directory, so the block above carries your
            username. A diagnosis bundle redacts a database root for exactly that
            reason; a config that runs a command cannot, because the path <em>is</em>
            {" "}the instruction. Check it before pasting it anywhere public.
          </Caveat>
        </>
      )}
    </section>
  );
}

/** Which database the generated config names.
 *
 *  Pinned by default, and the argument is the server's own: an agent host reads its
 *  config once at startup, so a config whose meaning changes when somebody clicks a
 *  row in another window would answer confidently about a database nobody chose. */
function Pin({ view, pin, onPin }: {
  view: AgentsView; pin: string; onPin: (v: string) => void;
}) {
  const following = pin === "none";
  return (
    <div className="mt-5">
      <div className="text-[10px] mono tracking-[0.14em] uppercase mb-2"
           style={{ color: "var(--dim)" }}>
        which database
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="seg">
          <button onClick={() => onPin("active")} data-on={!following}
                  className="mono !px-3.5 text-[10px] tracking-[0.14em] uppercase">
            pin it
          </button>
          <button onClick={() => onPin("none")} data-on={following}
                  className="mono !px-3.5 text-[10px] tracking-[0.14em] uppercase">
            follow the console
          </button>
        </div>
        {!following && view.connections.length > 1 && (
          <select className="qin" value={pin === "active" ? "active" : pin}
                  onChange={(e) => onPin(e.target.value)}>
            <option value="active">the active connection</option>
            {view.connections.map((c) => (
              <option key={c.id} value={c.id}>{c.label}</option>
            ))}
          </select>
        )}
      </div>
      {view.pin.mode !== "none" && (
        <p className="text-[12px] leading-relaxed mt-2" style={{ color: "var(--dim)" }}>
          {view.pin.note}
        </p>
      )}
    </div>
  );
}

function HostBlock({ host }: { host: AgentHost }) {
  return (
    <div className="mt-4">
      {host.command_line && (
        <>
          <Row label="run this">
            <Copy value={host.command_line} what="the command" />
          </Row>
          <Pre>{host.command_line}</Pre>
        </>
      )}

      <Row label={host.command_line ? "or write it yourself" : "add this to"}>
        <span className="mono text-[11px] truncate" style={{ color: "var(--dim)" }}>
          {host.config_path}
        </span>
        <Copy value={host.config_path} what="the path" />
      </Row>
      <Pre>{host.json}</Pre>
      <div className="flex justify-end mt-1">
        <Copy value={host.json} what="the config" />
      </div>

      <p className="text-[12px] leading-relaxed mt-2" style={{ color: "var(--dim)" }}>
        {host.restart_note}
      </p>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 mt-4 mb-1.5">
      <div className="text-[10px] mono tracking-[0.14em] uppercase shrink-0"
           style={{ color: "var(--dim)" }}>
        {label}
      </div>
      <div className="flex items-center gap-1.5 min-w-0 ml-auto">{children}</div>
    </div>
  );
}

function Pre({ children }: { children: React.ReactNode }) {
  return (
    <pre className="mono text-[11px] leading-relaxed p-3.5 rounded-sm overflow-x-auto"
         style={{
           color: "var(--body)",
           background: "var(--surface-2, rgb(var(--index-rgb) / 0.05))",
           border: "1px solid var(--rule)",
         }}>
      {children}
    </pre>
  );
}
