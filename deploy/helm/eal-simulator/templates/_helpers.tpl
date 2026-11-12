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
CORTEXSIM_ALLOWED_HOSTS: every name the chart itself makes SimCore reachable by,
plus the operator's api.allowedHosts. SimCore's Host guard
(core/security/host_guard.py) refuses any other DNS name with 400
HOST_NOT_ALLOWED, and in a pod the process's own hostname is the pod name, so
without this list the Service name, every Ingress host and the Tailscale name
are all refused while the probes (pod IP) keep the pods Ready.
Lower-cased, de-duplicated, comma-joined: the format the guard parses.
*/}}
{{- define "eal-simulator.allowedHosts" -}}
{{- $svc := printf "%s-api" (include "eal-simulator.fullname" .) -}}
{{- $ns := .Values.namespace -}}
{{- $hosts := list $svc (printf "%s.%s" $svc $ns) (printf "%s.%s.svc" $svc $ns) (printf "%s.%s.svc.%s" $svc $ns (.Values.clusterDomain | default "cluster.local")) -}}
{{- if .Values.ingress.enabled -}}
{{- range .Values.ingress.hosts -}}
{{- if .host -}}
{{- $hosts = append $hosts (.host | toString) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if and .Values.tailscale.enabled .Values.tailscale.hostname -}}
{{- $hosts = append $hosts (.Values.tailscale.hostname | toString) -}}
{{- end -}}
{{- range .Values.api.allowedHosts -}}
{{- if . -}}
{{- $hosts = append $hosts (. | toString) -}}
{{- end -}}
{{- end -}}
{{- $clean := list -}}
{{- range $hosts -}}
{{- $clean = append $clean (. | lower | trim) -}}
{{- end -}}
{{- $clean | uniq | join "," -}}
{{- end -}}

{{/*
Keys the ConfigMap template sets itself. api.extraEnv may not repeat one: two
definitions of a key render as a duplicate mapping key, and which wins is then
up to the YAML parser. CORTEXSIM_SECRET is refused outright: it belongs in the
Secret, never in a ConfigMap.
*/}}
{{- define "eal-simulator.validateExtraEnv" -}}
{{- $managed := dict "CORTEXSIM_ENV" "api.env.CORTEXSIM_ENV" "CORTEXSIM_PORT" "api.env.CORTEXSIM_PORT" "CORTEXSIM_BIND_ADDRESS" "api.bindAddress" "CORTEXSIM_ALLOWED_HOSTS" "api.allowedHosts" "CORTEXSIM_TRUSTED_PROXIES" "api.trustedProxies" "EAL_AUDIT_STDOUT" "audit.stdout" "EAL_AUDIT_FILE_ENABLED" "audit.file.enabled" "EAL_AUDIT_FILE_PATH" "audit.file.path" "EAL_REDIS_URL" "redis.enabled" -}}
{{- range $key, $value := (.Values.api.extraEnv | default dict) -}}
{{- if eq $key "CORTEXSIM_SECRET" -}}
{{- fail "api.extraEnv must not carry CORTEXSIM_SECRET: extraEnv is rendered into a ConfigMap, which is not a secret store. Supply the master key through masterSecret.existingSecret or masterSecret.value." -}}
{{- end -}}
{{- if hasKey $managed $key -}}
{{- fail (printf "api.extraEnv.%s would define %s twice: the chart already sets it. Use %s instead." $key $key (get $managed $key)) -}}
{{- end -}}
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
