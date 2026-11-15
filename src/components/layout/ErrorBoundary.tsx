import { Component, type ErrorInfo, type ReactNode } from "react";
import { PageLoadError } from "@/components/layout/PageLoadError";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

/** Catches lazy-route chunk failures so a deploy does not leave a blank shell. */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo): void {
    // Reload is the recovery path for a stale chunk after deployment.
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return <PageLoadError />;
  }
}
