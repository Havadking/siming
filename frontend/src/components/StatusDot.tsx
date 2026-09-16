import type { Status } from '../api'

export const STATUS_TONE: Record<Status, 'ok' | 'warn' | 'bad' | 'info' | 'neutral'> = {
  running: 'ok',
  starting: 'warn',
  unhealthy: 'warn',
  restarting: 'warn',
  stopped: 'neutral',
  exited: 'bad',
  crashed: 'bad',
  external: 'info',
  error: 'bad',
}

export const STATUS_LABEL: Record<Status, string> = {
  running: '在线',
  starting: '启动中',
  unhealthy: '端口没开',
  restarting: '等待重启',
  stopped: '已停止',
  exited: '异常退出',
  crashed: '已崩溃',
  external: '外部实例',
  error: '配置错误',
}

/** 状态点：running / starting 带脉冲。 */
export function StatusDot({ status }: { status: Status }) {
  const pulse = status === 'starting' || status === 'restarting'
  return <span className={`sdot ${STATUS_TONE[status]}${pulse ? ' pulse' : ''}`} aria-hidden />
}
