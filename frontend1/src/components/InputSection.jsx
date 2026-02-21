    // ─────────────────────────────────────────────────────────
// RIFT 2026 — Section 1: Input Form
// ─────────────────────────────────────────────────────────
import { useRef } from "react";
import { useAgent, useAgentDispatch } from "../context/AgentContext";
import { triggerAnalysis, connectSSE } from "../services/api";

export default function InputSection() {
  const state = useAgent();
  const dispatch = useAgentDispatch();
  const sseRef = useRef(null);

  const isDisabled = state.status === "loading" || state.status === "running";

  async function handleSubmit(e) {
    e.preventDefault();

    if (!state.repoUrl || !state.teamName || !state.leaderName) return;

    dispatch({ type: "SET_LOADING" });

    try {
      // 1. Trigger the backend
      const result = await triggerAnalysis({
        repoUrl: state.repoUrl,
        teamName: state.teamName,
        leaderName: state.leaderName,
      });

      dispatch({
        type: "START_RUN",
        runId: result.run_id,
        branchName: result.branch_name,
        maxIterations: result.max_iterations || 50,
      });

      // 2. Connect to SSE stream
      if (sseRef.current) sseRef.current.close();

      sseRef.current = connectSSE(result.run_id, {
        onProgress: (data) => {
          dispatch({
            type: "ADD_PROGRESS",
            message: data.message || data.phase || JSON.stringify(data),
          });
        },
        onFix: (data) => {
          dispatch({ type: "ADD_FIX", fix: data });
        },
        onIteration: (data) => {
          dispatch({ type: "ADD_ITERATION", iteration: data });
        },
        onComplete: (data) => {
          dispatch({ type: "COMPLETE", data });
        },
        onError: (data) => {
          dispatch({ type: "ERROR", message: data.message || "Unknown error" });
        },
      });
    } catch (err) {
      dispatch({ type: "ERROR", message: err.message });
    }
  }

  return (
    <section className="bg-gray-900 rounded-2xl p-6 border border-gray-800 shadow-lg">
      <h2 className="text-xl font-bold mb-4 text-cyan-400">
        Analyze Repository
      </h2>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* GitHub URL */}
        <div>
          <label className="block text-sm font-medium text-gray-400 mb-1">
            GitHub Repository URL
          </label>
          <input
            type="url"
            placeholder="https://github.com/org/repo"
            value={state.repoUrl}
            onChange={(e) =>
              dispatch({
                type: "SET_INPUT",
                field: "repoUrl",
                value: e.target.value,
              })
            }
            disabled={isDisabled}
            required
            className="w-full px-4 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent disabled:opacity-50"
          />
        </div>

        {/* Team Name */}
        <div>
          <label className="block text-sm font-medium text-gray-400 mb-1">
            Team Name
          </label>
          <input
            type="text"
            placeholder="e.g. RIFT ORGANISERS"
            value={state.teamName}
            onChange={(e) =>
              dispatch({
                type: "SET_INPUT",
                field: "teamName",
                value: e.target.value,
              })
            }
            disabled={isDisabled}
            required
            className="w-full px-4 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent disabled:opacity-50"
          />
        </div>

        {/* Leader Name */}
        <div>
          <label className="block text-sm font-medium text-gray-400 mb-1">
            Team Leader Name
          </label>
          <input
            type="text"
            placeholder="e.g. Saiyam Kumar"
            value={state.leaderName}
            onChange={(e) =>
              dispatch({
                type: "SET_INPUT",
                field: "leaderName",
                value: e.target.value,
              })
            }
            disabled={isDisabled}
            required
            className="w-full px-4 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:border-transparent disabled:opacity-50"
          />
        </div>

        {/* Submit Button */}
        <button
          type="submit"
          disabled={isDisabled}
          className="w-full py-3 px-6 bg-gradient-to-r from-cyan-500 to-blue-600 text-white font-bold rounded-lg transition-all hover:from-cyan-400 hover:to-blue-500 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {isDisabled ? (
            <>
              <Spinner />
              {state.status === "loading"
                ? "Starting agent..."
                : "Agent running..."}
            </>
          ) : (
            <>
              <RocketIcon />
              Analyze Repository
            </>
          )}
        </button>
      </form>

      {/* Error display */}
      {state.status === "error" && (
        <div className="mt-4 p-3 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
          {state.errorMessage}
        </div>
      )}

      {/* Live progress log */}
      {state.progressMessages.length > 0 && (
        <div className="mt-4 max-h-40 overflow-y-auto bg-gray-950 rounded-lg p-3 space-y-1 border border-gray-800">
          {state.progressMessages.map((msg, i) => (
            <p key={i} className="text-xs text-gray-400 font-mono">
              <span className="text-cyan-500">[{String(i + 1).padStart(2, "0")}]</span>{" "}
              {msg}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function Spinner() {
  return (
    <svg
      className="animate-spin h-5 w-5"
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

function RocketIcon() {
  return (
    <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.59 14.37a6 6 0 01-5.84 7.38v-4.8m5.84-2.58a14.98 14.98 0 003.46-1.25M15.59 14.37a6 6 0 00-5.84-7.38v4.8m5.84 2.58L12 21.75 3.75 14.37m0 0A6 6 0 019.59 7a6 6 0 005.84 7.38" />
    </svg>
  );
}
