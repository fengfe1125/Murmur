# androidx.security:security-crypto brings com.google.crypto.tink, whose
# bytecode references errorprone annotations that ship compile-time only.
# They are never needed at runtime (annotations are CLASS-retention markers).
-dontwarn com.google.errorprone.annotations.CanIgnoreReturnValue
-dontwarn com.google.errorprone.annotations.CheckReturnValue
-dontwarn com.google.errorprone.annotations.Immutable
-dontwarn com.google.errorprone.annotations.RestrictedApi
