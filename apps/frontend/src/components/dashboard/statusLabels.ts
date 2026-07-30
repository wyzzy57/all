export type DashboardStatusContext = "pipeline" | "dataset" | "service" | "generic";

const commonLabels: Record<string, string> = {
  cancelled: "已取消",
  completed: "已完成",
  degraded: "性能下降",
  deploying: "部署中",
  disabled: "已禁用",
  error: "错误",
  failed: "运行失败",
  interrupted: "运行中止",
  offline: "离线",
  online: "在线",
  pending: "等待中",
  processing: "处理中",
  queued: "排队中",
  running: "运行中",
  stale: "状态过期",
  starting: "启动中",
  stopped: "已停止",
  success: "运行成功",
  training: "训练中",
  unknown: "未知",
  warning: "告警",
};

const contextLabels: Record<DashboardStatusContext, Record<string, string>> = {
  pipeline: {
    draft: "配置中",
    ready: "配置中",
  },
  dataset: {
    created: "待校验",
    ready: "可用",
    validated: "已校验",
  },
  service: {
    healthy: "健康",
    ready: "就绪",
    unhealthy: "异常",
    unknown: "检查重试中",
  },
  generic: {},
};

export function dashboardStatusLabel(label: string, context: DashboardStatusContext = "generic") {
  const normalized = label.trim().toLowerCase().replace(/[\s_-]+/g, "");
  return contextLabels[context][normalized] ?? commonLabels[normalized] ?? label;
}
