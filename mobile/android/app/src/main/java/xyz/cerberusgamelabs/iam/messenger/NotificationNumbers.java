package xyz.cerberusgamelabs.iam.messenger;

/** JSON integers may be Integer or Long; reject coercion and unsafe JS values. */
final class NotificationNumbers {
    static long integer(Object value) {
        if (!(value instanceof Integer) && !(value instanceof Long)) throw new IllegalArgumentException();
        long result=((Number)value).longValue();
        if(result<0 || result>9007199254740991L)throw new IllegalArgumentException();
        return result;
    }
}
