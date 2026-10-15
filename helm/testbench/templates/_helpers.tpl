{{- define "testbench.name" -}}testbench{{- end }}
{{- define "testbench.fullname" -}}{{ printf "%s-testbench" .Release.Name | trunc 63 | trimSuffix "-" }}{{- end }}
{{- define "testbench.labels" -}}
app.kubernetes.io/name: testbench
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end }}
{{- define "testbench.image" -}}
{{- $registry := trimSuffix "/" (default "" .root.Values.global.imageRegistry) -}}
{{- if hasKey .image "registry" -}}
{{- $registry = trimSuffix "/" .image.registry -}}
{{- end -}}
{{- $repository := .image.repository -}}
{{- if $registry -}}
{{- $repository = printf "%s/%s" $registry (trimPrefix "/" $repository) -}}
{{- end -}}
{{- if .image.digest -}}
{{ printf "%s@%s" $repository .image.digest }}
{{- else -}}
{{ printf "%s:%s" $repository .image.tag }}
{{- end -}}
{{- end }}
{{- define "testbench.imagePullPolicy" -}}
{{- default .root.Values.global.imagePullPolicy .image.pullPolicy -}}
{{- end }}
{{- define "testbench.imagePullSecretNames" -}}
{{- $names := list -}}
{{- range .global -}}
  {{- $names = append $names .name -}}
{{- end -}}
{{- range .local -}}
  {{- $names = append $names . -}}
{{- end -}}
{{- join "," (uniq $names) -}}
{{- end }}
{{- define "testbench.globalCaEnv" -}}
{{- if .Values.global.tls.existingConfigMap }}
- name: SSL_CERT_FILE
  value: /etc/testbench/ca/{{ required "global.tls.key is required when global.tls.existingConfigMap is set" .Values.global.tls.key }}
- name: REQUESTS_CA_BUNDLE
  value: /etc/testbench/ca/{{ .Values.global.tls.key }}
- name: NODE_EXTRA_CA_CERTS
  value: /etc/testbench/ca/{{ .Values.global.tls.key }}
- name: AWS_CA_BUNDLE
  value: /etc/testbench/ca/{{ .Values.global.tls.key }}
- name: CURL_CA_BUNDLE
  value: /etc/testbench/ca/{{ .Values.global.tls.key }}
{{- end }}
{{- end }}
{{- define "testbench.globalCaMount" -}}
{{- if .Values.global.tls.existingConfigMap }}
- name: global-ca
  mountPath: /etc/testbench/ca
  readOnly: true
{{- end }}
{{- end }}
{{- define "testbench.globalCaVolume" -}}
{{- if .Values.global.tls.existingConfigMap }}
- name: global-ca
  configMap:
    name: {{ .Values.global.tls.existingConfigMap | quote }}
    items:
      - key: {{ required "global.tls.key is required when global.tls.existingConfigMap is set" .Values.global.tls.key | quote }}
        path: {{ .Values.global.tls.key | quote }}
{{- end }}
{{- end }}
{{- define "testbench.databaseEnv" -}}
- name: TB_DB_HOST
  value: {{ required "database.host is required" .Values.database.host | quote }}
- name: TB_DB_PORT
  value: {{ .Values.database.port | quote }}
- name: TB_DB_NAME
  value: {{ required "database.name is required" .Values.database.name | quote }}
- name: TB_DB_SSLMODE
  value: {{ .Values.database.sslmode | quote }}
- name: TB_DB_USER
  valueFrom:
    secretKeyRef:
      name: {{ required "database.credentials.existingSecret is required" .Values.database.credentials.existingSecret }}
      key: {{ required "database.credentials.usernameKey is required" .Values.database.credentials.usernameKey }}
- name: TB_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ required "database.credentials.existingSecret is required" .Values.database.credentials.existingSecret }}
      key: {{ required "database.credentials.passwordKey is required" .Values.database.credentials.passwordKey }}
{{- end }}
{{- define "testbench.scanNamespace" -}}{{ default .Release.Namespace .Values.plugins.networkScan.worker.namespace }}{{- end }}
{{- define "testbench.researchNamespace" -}}{{ default .Release.Namespace .Values.plugins.deviceInfo.research.namespace }}{{- end }}
{{- define "testbench.rebootNamespace" -}}{{ default .Release.Namespace .Values.plugins.reboot.worker.namespace }}{{- end }}
