{{/*
Expand the name of the chart.
*/}}
{{- define "agentorchestration.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "agentorchestration.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Chart label (name + version).
*/}}
{{- define "agentorchestration.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every resource.
*/}}
{{- define "agentorchestration.labels" -}}
helm.sh/chart: {{ include "agentorchestration.chart" . }}
app.kubernetes.io/name: {{ include "agentorchestration.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Image reference helper — registry/name:tag
*/}}
{{- define "agentorchestration.image" -}}
{{- $reg := .root.Values.image.registry -}}
{{- $name := .name -}}
{{- $tag := .root.Values.image.tag -}}
{{- if $reg -}}
{{- printf "%s/%s:%s" $reg $name $tag -}}
{{- else -}}
{{- printf "%s:%s" $name $tag -}}
{{- end -}}
{{- end }}

{{/*
Cluster-internal Service name helpers
*/}}
{{- define "agentorchestration.backendSvcName" -}}
{{- printf "%s-backend" (include "agentorchestration.fullname" .) }}
{{- end }}

{{- define "agentorchestration.taskAgentSvcName" -}}
{{- printf "%s-task-agent" (include "agentorchestration.fullname" .) }}
{{- end }}

{{- define "agentorchestration.mcpTodosSvcName" -}}
{{- printf "%s-mcp-todos-server" (include "agentorchestration.fullname" .) }}
{{- end }}

{{- define "agentorchestration.frontendSvcName" -}}
{{- printf "%s-frontend" (include "agentorchestration.fullname" .) }}
{{- end }}

{{/*
Resolved TASK_AGENT_URL — uses values override or falls back to cluster-internal DNS.
*/}}
{{- define "agentorchestration.taskAgentUrl" -}}
{{- if .Values.backend.env.TASK_AGENT_URL -}}
{{- .Values.backend.env.TASK_AGENT_URL -}}
{{- else -}}
{{- printf "http://%s:%d" (include "agentorchestration.taskAgentSvcName" .) (.Values.taskAgent.service.port | int) -}}
{{- end -}}
{{- end }}

{{/*
Resolved PUBLIC_URL for the task-agent.
*/}}
{{- define "agentorchestration.taskAgentPublicUrl" -}}
{{- if .Values.taskAgent.env.PUBLIC_URL -}}
{{- .Values.taskAgent.env.PUBLIC_URL -}}
{{- else -}}
{{- printf "http://%s:%d" (include "agentorchestration.taskAgentSvcName" .) (.Values.taskAgent.service.port | int) -}}
{{- end -}}
{{- end }}

{{/*
Resolved MCP_TODOS_URL for the task-agent.
*/}}
{{- define "agentorchestration.mcpTodosUrl" -}}
{{- if .Values.taskAgent.env.MCP_TODOS_URL -}}
{{- .Values.taskAgent.env.MCP_TODOS_URL -}}
{{- else -}}
{{- printf "http://%s:%d/mcp" (include "agentorchestration.mcpTodosSvcName" .) (.Values.mcpTodosServer.service.port | int) -}}
{{- end -}}
{{- end }}
