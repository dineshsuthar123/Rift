// ─────────────────────────────────────────────────────────
// RIFT 2026 — Global State (React Context + useReducer)
// ─────────────────────────────────────────────────────────
import { createContext, useContext, useReducer } from "react";

const AgentContext = createContext(null);
const AgentDispatchContext = createContext(null);

const initialState = {
  // Input
  repoUrl: "",
  teamName: "",
  leaderName: "",

  // Run state
  runId: null,
  status: "idle", // idle | loading | running | passed | failed | error
  branchName: "",
  maxIterations: 50,

  // Progress
  progressMessages: [],

  // Results
  fixes: [],
  timeline: [],
  score: null,
  timing: null,
  totalErrors: 0,
  totalFixes: 0,
  commitCount: 0,

  // Error
  errorMessage: "",
};

function reducer(state, action) {
  switch (action.type) {
    case "SET_INPUT":
      return { ...state, [action.field]: action.value };

    case "START_RUN":
      return {
        ...state,
        runId: action.runId,
        branchName: action.branchName,
        maxIterations: action.maxIterations || state.maxIterations,
        status: "running",
        progressMessages: [],
        fixes: [],
        timeline: [],
        score: null,
        timing: null,
        totalErrors: 0,
        totalFixes: 0,
        commitCount: 0,
        errorMessage: "",
      };

    case "ADD_PROGRESS":
      return {
        ...state,
        progressMessages: [...state.progressMessages, action.message],
      };

    case "ADD_FIX":
      return {
        ...state,
        fixes: [...state.fixes, action.fix],
      };

    case "ADD_ITERATION":
      return {
        ...state,
        timeline: [...state.timeline, action.iteration],
      };

    case "COMPLETE":
      return {
        ...state,
        status: action.data.status === "PASSED" ? "passed" : "failed",
        fixes: action.data.fixes || state.fixes,
        timeline: action.data.timeline || state.timeline,
        score: action.data.score || null,
        timing: action.data.timing || null,
        totalErrors: action.data.total_errors || 0,
        totalFixes: action.data.total_fixes || 0,
        commitCount: action.data.commit_count || 0,
      };

    case "ERROR":
      return {
        ...state,
        status: "error",
        errorMessage: action.message,
      };

    case "SET_LOADING":
      return { ...state, status: "loading" };

    case "RESET":
      return { ...initialState };

    default:
      return state;
  }
}

export function AgentProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  return (
    <AgentContext.Provider value={state}>
      <AgentDispatchContext.Provider value={dispatch}>
        {children}
      </AgentDispatchContext.Provider>
    </AgentContext.Provider>
  );
}

export function useAgent() {
  return useContext(AgentContext);
}

export function useAgentDispatch() {
  return useContext(AgentDispatchContext);
}
