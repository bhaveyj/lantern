function Get-LanternSigningCertificate {
    $thumbprint = $env:LANTERN_WINDOWS_CERT_THUMBPRINT
    if (-not $thumbprint -or $thumbprint -notmatch '^[A-Fa-f0-9]{40}$') {
        throw 'Release signing requires LANTERN_WINDOWS_CERT_THUMBPRINT for your code-signing certificate in CurrentUser\My.'
    }
    $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$thumbprint" -ErrorAction SilentlyContinue
    $now = Get-Date
    $usages = @($certificate.Extensions | Where-Object { $_ -is [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] } | ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
    if (-not $certificate -or -not $certificate.HasPrivateKey -or $certificate.NotBefore -gt $now -or $certificate.NotAfter -le $now -or '1.3.6.1.5.5.7.3.3' -notin $usages) {
        throw 'Release signing requires a valid code-signing certificate with its private key.'
    }
    return $certificate
}

function Set-LanternReleaseSignature([string]$Path) {
    $certificate = Get-LanternSigningCertificate
    $signature = Set-AuthenticodeSignature -LiteralPath $Path -Certificate $certificate -HashAlgorithm SHA256 -TimestampServer 'https://timestamp.digicert.com'
    if ($signature.Status -ne 'Valid' -or -not $signature.TimeStamperCertificate) {
        throw "Release signature or timestamp validation failed for $Path"
    }
    $verified = Get-AuthenticodeSignature -LiteralPath $Path
    if ($verified.Status -ne 'Valid' -or $verified.SignerCertificate.Thumbprint -ne $certificate.Thumbprint) {
        throw "Release signature verification failed for $Path"
    }
}
