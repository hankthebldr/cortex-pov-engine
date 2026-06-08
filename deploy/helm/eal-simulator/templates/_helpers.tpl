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

{{/*
Name of the Secret that holds CORTEXSIM_SECRET. Uses the operator-provided
existingSecret when set; otherwise the chart-managed secret created from
masterSecret.value.
*/}}
{{- define "eal-simulator.masterSecretName" -}}
{{- if .Values.masterSecret.existingSecret -}}
{{- .Values.masterSecret.existingSecret -}}
{{- else -}}
{{- printf "%s-master" (include "eal-simulator.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Fail-fast guard: in production the master key MUST be supplied (either an
existingSecret or an inline value). Mirrors core/config.py validate_master_key()
so the failure surfaces at `helm install` time instead of as a pod crash-loop.
*/}}
{{- define "eal-simulator.validateMasterSecret" -}}
{{- if and (eq (.Values.api.env.CORTEXSIM_ENV | toString) "production") (not .Values.masterSecret.existingSecret) (not .Values.masterSecret.value) -}}
{{- fail "CORTEXSIM_ENV=production requires a master key. Set masterSecret.existingSecret to a pre-created Secret, or pass masterSecret.value (e.g. --set masterSecret.value=$(openssl rand -hex 32)). See values.yaml masterSecret and core/config.py validate_master_key()." -}}
{{- end -}}
{{- end -}}
