{{/*
Expand the name of the chart.
*/}}
{{- define "eal-simulator.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name. Concatenates release + chart for uniqueness.
*/}}
{{- define "eal-simulator.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels.
*/}}
{{- define "eal-simulator.labels" -}}
app.kubernetes.io/name: {{ include "eal-simulator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
cortexsim.paloaltonetworks.com/component: eal-simulator
{{- end -}}

{{/*
Selector labels (used in spec.selector).
*/}}
{{- define "eal-simulator.selectorLabels" -}}
app.kubernetes.io/name: {{ include "eal-simulator.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
