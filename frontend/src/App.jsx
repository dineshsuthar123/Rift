// ─────────────────────────────────────────────────────────
// RIFT 2026 — Main App Component
// ─────────────────────────────────────────────────────────
import { AgentProvider } from "./context/AgentContext";
import InputSection from "./components/InputSection";
import RunSummary from "./components/RunSummary";
import ScoreBreakdown from "./components/ScoreBreakdown";
import FixesTable from "./components/FixesTable";
import CICDTimeline from "./components/CICDTimeline";

export default function App() {
  return (
    <AgentProvider>
      <div className="min-h-screen bg-gray-950">
        {/* Header */}
        <header className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 py-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="h-8 w-8 rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center text-white font-black text-sm">
                R
              </div>
              <div>
                <h1 className="text-lg font-bold text-white tracking-tight">
                  RIFT 2026
                </h1>
                <p className="text-xs text-gray-500">
                  Autonomous CI/CD Healing Agent
                </p>
              </div>
            </div>
            <span className="text-xs text-gray-600 font-mono">
              AIML Track
            </span>
          </div>
        </header>

        {/* Main content */}
        <main className="max-w-7xl mx-auto px-4 py-8 space-y-6">
          {/* Row 1: Input + Summary side by side on desktop */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <InputSection />
            <RunSummary />
          </div>

          {/* Row 2: Score + Timeline side by side */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <ScoreBreakdown />
            <CICDTimeline />
          </div>

          {/* Row 3: Fixes table (full width) */}
          <FixesTable />
        </main>

        {/* Footer */}
        <footer className="border-t border-gray-800 mt-12 py-6 text-center text-xs text-gray-600">
          Built for RIFT 2026 Hackathon — AI/ML Track
        </footer>
      </div>
    </AgentProvider>
  );
}
