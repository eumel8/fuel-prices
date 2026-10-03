{{/* Chart-Name, ueberschreibbar. */}}
{{- define "fuel-prices.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "fuel-prices.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "fuel-prices.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "fuel-prices.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{- define "fuel-prices.selectorLabels" -}}
app.kubernetes.io/name: {{ include "fuel-prices.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "fuel-prices.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "fuel-prices.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "fuel-prices.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{/* Name des PVC: eingebunden von Deployment und beiden CronJobs. */}}
{{- define "fuel-prices.pvcName" -}}
{{- if .Values.persistence.existingClaim -}}
{{- .Values.persistence.existingClaim -}}
{{- else -}}
{{- printf "%s-data" (include "fuel-prices.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* Optionaler Basic-Auth-Secret-Name, sonst leer. */}}
{{- define "fuel-prices.authSecretName" -}}
{{- if and .Values.auth.enabled .Values.auth.existingSecret -}}
{{- .Values.auth.existingSecret -}}
{{- end -}}
{{- end -}}

{{/* Secret-Name fuer den Tankerkönig-Key: bestehendes Secret bevorzugt,
     sonst das aus tankerkoenig.apiKey erzeugte, sonst leer. */}}
{{- define "fuel-prices.tankerkoenigSecretName" -}}
{{- if .Values.tankerkoenig.existingSecret -}}
{{- .Values.tankerkoenig.existingSecret -}}
{{- else if .Values.tankerkoenig.apiKey -}}
{{- printf "%s-tankerkoenig" (include "fuel-prices.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* Env-Variablen fuer Ingest-Jobs. */}}
{{- define "fuel-prices.ingestEnv" -}}
- name: FUEL_DATA_DIR
  value: /data
- name: FUEL_DB_PATH
  value: /data/fuel.db
{{- $secret := include "fuel-prices.tankerkoenigSecretName" . }}
{{- if $secret }}
- name: TANKERKOENIG_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $secret }}
      key: {{ .Values.tankerkoenig.existingSecretKey }}
      optional: true
{{- end }}
{{- end -}}