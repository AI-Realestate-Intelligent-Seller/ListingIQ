from app.location.service import get_zipcodes


def test_alpine_arizona_location_record():
    alpine = [row for row in get_zipcodes("Arizona") if row["city"] == "Alpine"]

    assert alpine == [{
        "city": "Alpine",
        "zip": "85920",
        "state": "Arizona",
        "state_code": "AZ",
        "county": "Apache",
        "latitude": "33.85995",
        "longitude": "-109.1753",
    }]
