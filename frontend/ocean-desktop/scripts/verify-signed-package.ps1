param(
  [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
  [string[]] $Paths
)

$ErrorActionPreference = 'Stop'

foreach ($Path in $Paths) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Signed release path is missing: $Path"
  }
  $signature = Get-AuthenticodeSignature -LiteralPath $Path
  if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
    throw "Authenticode verification failed for $Path with status $($signature.Status)."
  }
  if ($null -eq $signature.SignerCertificate -or [string]::IsNullOrWhiteSpace($signature.SignerCertificate.Subject)) {
    throw "Authenticode verification returned no signer identity for $Path."
  }
  [PSCustomObject]@{
    path = Split-Path -Leaf $Path
    subject = $signature.SignerCertificate.Subject
    thumbprint = $signature.SignerCertificate.Thumbprint
  } | ConvertTo-Json -Compress
}
