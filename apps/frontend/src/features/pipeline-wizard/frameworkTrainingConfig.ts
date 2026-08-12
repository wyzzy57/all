import { parse, stringify } from "yaml";

import type {
  FrameworkModelCapabilityRecord,
  FrameworkParameterCapabilityRecord,
} from "@/api/client";

export type FrameworkTrainingParams = Record<string, unknown>;

export type FrameworkTrainingYamlResult = {
  params: FrameworkTrainingParams;
  errors: string[];
};

function isRecord(value: unknown): value is FrameworkTrainingParams {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function templateParams(model: FrameworkModelCapabilityRecord): FrameworkTrainingParams {
  if (!model.config_template?.trim()) return {};
  try {
    const value = parse(model.config_template);
    return isRecord(value) ? value : {};
  } catch {
    return {};
  }
}

function parameterMap(parameters: FrameworkParameterCapabilityRecord[]) {
  return new Map(parameters.map((parameter) => [parameter.name, parameter]));
}

export function buildFrameworkTrainingConfig(
  model: FrameworkModelCapabilityRecord,
  parameters: FrameworkParameterCapabilityRecord[],
  current: FrameworkTrainingParams = {},
): FrameworkTrainingParams {
  const template = templateParams(model);
  const known = new Set(parameters.map((parameter) => parameter.name));
  const managed = new Set(model.managed_parameter_names ?? []);
  const result: FrameworkTrainingParams = { ...template };

  for (const parameter of parameters) {
    if (!(parameter.name in result) && parameter.default !== undefined) {
      result[parameter.name] = parameter.default;
    }
  }
  for (const [name, value] of Object.entries(current)) {
    const accepted = known.size ? known.has(name) : name in template;
    if (accepted && !managed.has(name)) result[name] = value;
  }
  return result;
}

function validateValue(parameter: FrameworkParameterCapabilityRecord, value: unknown): string[] {
  const errors: string[] = [];
  const typeValid =
    (parameter.value_type === "string" && typeof value === "string") ||
    (parameter.value_type === "boolean" && typeof value === "boolean") ||
    (parameter.value_type === "number" && typeof value === "number" && Number.isFinite(value)) ||
    (parameter.value_type === "integer" && typeof value === "number" && Number.isInteger(value));
  if (!typeValid) {
    const labels = { string: "字符串", boolean: "布尔值", number: "数字", integer: "整数" };
    return [`${parameter.name} 必须是${labels[parameter.value_type]}`];
  }
  if (typeof value === "number" && parameter.minimum != null && value < parameter.minimum) {
    errors.push(`${parameter.name} 不能小于 ${parameter.minimum}`);
  }
  if (typeof value === "number" && parameter.maximum != null && value > parameter.maximum) {
    errors.push(`${parameter.name} 不能大于 ${parameter.maximum}`);
  }
  if (parameter.choices?.length && !parameter.choices.includes(value as never)) {
    errors.push(`${parameter.name} 必须是以下选项之一：${parameter.choices.join("、")}`);
  }
  return errors;
}

export function validateFrameworkTrainingParams(
  params: FrameworkTrainingParams,
  model: FrameworkModelCapabilityRecord,
  parameters: FrameworkParameterCapabilityRecord[],
): string[] {
  const definitions = parameterMap(parameters);
  const managed = new Set(model.managed_parameter_names ?? []);
  const errors: string[] = [];
  for (const [name, value] of Object.entries(params)) {
    if (managed.has(name)) {
      errors.push(`系统托管参数不可修改：${name}`);
      continue;
    }
    const parameter = definitions.get(name);
    if (!parameter) {
      errors.push(`未知参数：${name}`);
      continue;
    }
    errors.push(...validateValue(parameter, value));
  }
  for (const parameter of parameters) {
    if (parameter.required && !(parameter.name in params)) errors.push(`缺少必填参数：${parameter.name}`);
  }
  return errors;
}

export function parseFrameworkTrainingYaml(
  source: string,
  model: FrameworkModelCapabilityRecord,
  parameters: FrameworkParameterCapabilityRecord[],
): FrameworkTrainingYamlResult {
  let parsed: unknown;
  try {
    parsed = parse(source);
  } catch (error) {
    return { params: {}, errors: [`YAML 语法错误：${error instanceof Error ? error.message : String(error)}`] };
  }
  if (!isRecord(parsed)) return { params: {}, errors: ["YAML 顶层必须是参数对象"] };
  return { params: parsed, errors: validateFrameworkTrainingParams(parsed, model, parameters) };
}

export function stringifyFrameworkTrainingConfig(params: FrameworkTrainingParams): string {
  return stringify(params, { lineWidth: 0 });
}
