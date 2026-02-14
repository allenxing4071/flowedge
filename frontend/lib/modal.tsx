/**
 * 命令式弹窗工具（Modal.confirm / Modal.alert / Modal.danger 等）
 * 适配 FlowEdge 深色交易驾驶舱主题。
 */
'use client';

import { createRoot } from 'react-dom/client';

// ─── 配置接口 ───
interface ConfirmConfig {
  title: string;
  content?: string;
  okText?: string;
  cancelText?: string;
  type?: 'warning' | 'info' | 'success' | 'danger';
  onOk?: () => void | Promise<void>;
  onCancel?: () => void;
}

interface AlertConfig {
  title: string;
  content?: string;
  okText?: string;
  type?: 'warning' | 'info' | 'success' | 'danger';
}

// ─── FlowEdge 色彩映射 ───
const typeColors: Record<string, string> = {
  warning: '#ffab00',
  info: '#448aff',
  success: '#00e676',
  danger: '#ff1744',
};

// ─── 内联 SVG 图标 ───
const Icons: Record<string, JSX.Element> = {
  warning: (
    <svg className="w-6 h-6" style={{ color: '#ffab00' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
    </svg>
  ),
  info: (
    <svg className="w-6 h-6" style={{ color: '#448aff' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  success: (
    <svg className="w-6 h-6" style={{ color: '#00e676' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  danger: (
    <svg className="w-6 h-6" style={{ color: '#ff1744' }} fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
    </svg>
  ),
  close: (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
    </svg>
  ),
};

// ─── Confirm 弹窗组件 ───
function ConfirmModal({ config, onClose }: { config: ConfirmConfig; onClose: () => void }) {
  const { title, content, okText = '确定', cancelText = '取消', type = 'warning', onOk, onCancel } = config;
  const btnColor = typeColors[type] || typeColors.warning;

  const handleOk = async () => {
    try { if (onOk) await onOk(); onClose(); } catch (e) { console.error('确认操作失败:', e); }
  };
  const handleCancel = () => { if (onCancel) onCancel(); onClose(); };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', animation: 'fe-modal-fade .15s ease' }}>
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(4px)' }} onClick={handleCancel} />
      <div style={{ position: 'relative', background: '#0c0c14', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, boxShadow: '0 25px 50px -12px rgba(0,0,0,0.6)', maxWidth: 420, width: '100%', margin: '0 16px', animation: 'fe-modal-scale .2s ease' }}>
        <button onClick={handleCancel} style={{ position: 'absolute', top: 16, right: 16, padding: 4, color: '#555570', background: 'transparent', border: 'none', borderRadius: 8, cursor: 'pointer' }}>{Icons.close}</button>
        <div style={{ padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
            <div style={{ flexShrink: 0, marginTop: 2 }}>{Icons[type]}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <h3 style={{ fontSize: 18, fontWeight: 600, color: '#f0f0f5', margin: '0 0 8px 0' }}>{title}</h3>
              {content && <p style={{ fontSize: 14, color: '#8888a0', lineHeight: 1.6, whiteSpace: 'pre-wrap', margin: 0 }}>{content}</p>}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 12, padding: '16px 24px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          <button onClick={handleCancel} style={{ padding: '8px 16px', fontSize: 14, fontWeight: 500, color: '#8888a0', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: 8, cursor: 'pointer' }}>{cancelText}</button>
          <button onClick={handleOk} style={{ padding: '8px 16px', fontSize: 14, fontWeight: 500, color: '#fff', background: btnColor, border: 'none', borderRadius: 8, cursor: 'pointer' }}>{okText}</button>
        </div>
      </div>
      <style>{`
        @keyframes fe-modal-fade { from{opacity:0} to{opacity:1} }
        @keyframes fe-modal-scale { from{opacity:0;transform:scale(.95) translateY(8px)} to{opacity:1;transform:scale(1) translateY(0)} }
      `}</style>
    </div>
  );
}

// ─── Alert 弹窗组件（仅确定按钮） ───
function AlertModal({ config, onClose }: { config: AlertConfig; onClose: () => void }) {
  const { title, content, okText = '确定', type = 'info' } = config;
  const btnColor = typeColors[type] || typeColors.info;

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', animation: 'fe-modal-fade .15s ease' }}>
      <div style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(4px)' }} onClick={onClose} />
      <div style={{ position: 'relative', background: '#0c0c14', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 12, boxShadow: '0 25px 50px -12px rgba(0,0,0,0.6)', maxWidth: 420, width: '100%', margin: '0 16px', animation: 'fe-modal-scale .2s ease' }}>
        <button onClick={onClose} style={{ position: 'absolute', top: 16, right: 16, padding: 4, color: '#555570', background: 'transparent', border: 'none', borderRadius: 8, cursor: 'pointer' }}>{Icons.close}</button>
        <div style={{ padding: 24 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
            <div style={{ flexShrink: 0, marginTop: 2 }}>{Icons[type]}</div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <h3 style={{ fontSize: 18, fontWeight: 600, color: '#f0f0f5', margin: '0 0 8px 0' }}>{title}</h3>
              {content && <p style={{ fontSize: 14, color: '#8888a0', lineHeight: 1.6, whiteSpace: 'pre-wrap', margin: 0 }}>{content}</p>}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', padding: '16px 24px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
          <button onClick={onClose} style={{ padding: '8px 20px', fontSize: 14, fontWeight: 500, color: '#fff', background: btnColor, border: 'none', borderRadius: 8, cursor: 'pointer' }}>{okText}</button>
        </div>
      </div>
      <style>{`
        @keyframes fe-modal-fade { from{opacity:0} to{opacity:1} }
        @keyframes fe-modal-scale { from{opacity:0;transform:scale(.95) translateY(8px)} to{opacity:1;transform:scale(1) translateY(0)} }
      `}</style>
    </div>
  );
}

// ─── 命令式 API ───
export function modalConfirm(config: ConfirmConfig): Promise<boolean> {
  return new Promise((resolve) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const handleClose = () => { root.unmount(); container.remove(); };
    root.render(
      <ConfirmModal
        config={{
          ...config,
          onOk: async () => { if (config.onOk) await config.onOk(); resolve(true); },
          onCancel: () => { if (config.onCancel) config.onCancel(); resolve(false); },
        }}
        onClose={handleClose}
      />
    );
  });
}

export function modalAlert(config: AlertConfig): Promise<void> {
  return new Promise((resolve) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const handleClose = () => { root.unmount(); container.remove(); resolve(); };
    root.render(<AlertModal config={config} onClose={handleClose} />);
  });
}

// ─── 快捷方法 ───
export const Modal = {
  confirm: modalConfirm,
  alert: modalAlert,

  warning: (config: Omit<ConfirmConfig, 'type'>) => modalConfirm({ ...config, type: 'warning' }),
  info: (config: Omit<ConfirmConfig, 'type'>) => modalConfirm({ ...config, type: 'info' }),
  success: (config: Omit<ConfirmConfig, 'type'>) => modalConfirm({ ...config, type: 'success' }),
  danger: (config: Omit<ConfirmConfig, 'type'>) => modalConfirm({ ...config, type: 'danger' }),

  alertSuccess: (config: Omit<AlertConfig, 'type'>) => modalAlert({ ...config, type: 'success' }),
  alertDanger: (config: Omit<AlertConfig, 'type'>) => modalAlert({ ...config, type: 'danger' }),
  alertWarning: (config: Omit<AlertConfig, 'type'>) => modalAlert({ ...config, type: 'warning' }),
};

export default Modal;
