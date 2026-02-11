/**
 * 全局错误边界 — 捕获子组件 render 错误，防止整页崩溃
 * 单个面板出错时只显示该面板的错误提示，不影响其他面板。
 */

'use client';

import React from 'react';

interface Props {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error(`[ErrorBoundary${this.props.name ? ': ' + this.props.name : ''}]`, error, info);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div className="card p-4 border border-bear/20 bg-bear/5">
          <div className="text-sm text-bear font-medium mb-1">
            {this.props.name ? `${this.props.name} 加载失败` : '组件加载失败'}
          </div>
          <div className="text-xs text-text-tertiary">
            {this.state.error?.message || '未知错误'}
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

/**
 * 安全面板包装器 — 快捷使用 ErrorBoundary
 */
export function SafePanel({ children, name }: { children: React.ReactNode; name: string }) {
  return (
    <ErrorBoundary name={name}>
      {children}
    </ErrorBoundary>
  );
}
