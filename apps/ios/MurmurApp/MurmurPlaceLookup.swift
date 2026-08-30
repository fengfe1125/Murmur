import CoreLocation
import Foundation

/// One coordinate in, one sentence a person would actually say out.
///
/// The reverse geocode happens on this phone.  What leaves is the *name* —
/// 「衡山路那家星巴克」 — never the coordinate: the server does no geocoding of
/// its own and keeps nothing but the anonymised ~110m fingerprint.
///
/// A protocol because `SystemPlaceLookup` needs the network, and a unit test
/// should not wait out a real round trip to find out what the room does with
/// an answer that never comes.
protocol MurmurPlaceLookup: Sendable {
    /// nil for every ordinary reason there is — no network, no result, the
    /// lookup ran long.  A missing place is not an error, it is Tuesday.
    func name(latitude: Double, longitude: Double) async -> String?
}

/// CLGeocoder.
///
/// Reverse-geocoding a coordinate the caller already holds needs **no location
/// permission**: 当年今日's existing photo-library authorization is the whole
/// budget, and no second system dialog is spent on this.
///
/// Called at the moment the photo is actually swiped up, never while the card
/// is merely on screen.  Resolving ahead would send Apple the coordinates of
/// photos the person only scrolled past.
struct SystemPlaceLookup: MurmurPlaceLookup {
    /// Long enough for a geocode on a thin connection, short enough that the
    /// send does not visibly wait on it.  The upload is what matters; the name
    /// is a bonus that either arrives in time or does not go at all.
    var timeoutSeconds: TimeInterval = 5

    /// Matches `MAX_PLACE_CHARS` in `murmur/moment.py`, which is where the
    /// string ends up.  Trimming here rather than there means the person's
    /// own phone decides what to say, not a truncation downstream.
    static let maximumCharacters = 60

    func name(latitude: Double, longitude: Double) async -> String? {
        // Apple throttles reverse geocoding and asks for one request in flight
        // at a time; one send is one request, on a geocoder that lives and dies
        // with this call.  CLPlacemark never leaves the closure — only the
        // composed String crosses back.
        let resolved = try? await withTimeout(seconds: timeoutSeconds) {
            let placemarks = try await CLGeocoder().reverseGeocodeLocation(
                CLLocation(latitude: latitude, longitude: longitude),
                preferredLocale: Locale(identifier: "zh_Hans_CN")
            )
            return placemarks.first.flatMap(Self.compose)
        }
        return resolved ?? nil
    }

    /// Composes the one line that goes up.
    ///
    /// The POI comes first because 「星巴克（衡山路店）」 is what he would call
    /// the place and 「衡山路 880 号」 is what a database calls it.  The district
    /// goes in front of it only when the POI does not already contain it:
    /// 「徐汇区 · 徐汇滨江」 is repetition wearing the clothes of detail.
    static func compose(_ placemark: CLPlacemark) -> String? {
        compose(
            areaOfInterest: placemark.areasOfInterest?.first,
            name: placemark.name,
            locality: placemark.locality,
            subLocality: placemark.subLocality
        )
    }

    /// Takes strings rather than a `CLPlacemark` because the decision it makes
    /// is about words, not about CoreLocation — and a placemark cannot be built
    /// in a test without going through the network that produced it.
    static func compose(
        areaOfInterest: String?, name: String?, locality: String?, subLocality: String?
    ) -> String? {
        let subject = (areaOfInterest ?? name)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let subject, !subject.isEmpty else { return nil }
        let area = [locality, subLocality]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && !subject.contains($0) }
        return String((area + [subject]).joined(separator: " · ")
            .prefix(maximumCharacters))
    }
}
