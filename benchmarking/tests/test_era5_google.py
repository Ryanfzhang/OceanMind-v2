"""Google mirror transport: original samples, restart and legacy archive compatibility."""
import datetime as dt
import json

import netCDF4
import numpy as np
import pytest

import download_all
import download_data
import download_services
import era5_google


class FakeStore:
    calls = []
    fail_hour = None
    origin = int((dt.datetime(1993, 5, 1) - dt.datetime(1900, 1, 1)).total_seconds() / 3600)

    def __init__(self):
        self.metadata = {
            '.zattrs': {'valid_time_start': '1940-01-01', 'valid_time_stop': '2026-05-31'},
            'time/.zattrs': {'units': 'hours since 1900-01-01 00:00:00', 'calendar': 'proleptic_gregorian'},
            'time/.zarray': {'chunks': [744]},
            'surface_sensible_heat_flux/.zattrs': {
                'units': 'J m**-2', '_ARRAY_DIMENSIONS': ['time', 'latitude', 'longitude']},
            'surface_sensible_heat_flux/.zarray': {'chunks': [1, 3, 4]},
        }

    def array(self, name, key):
        if name == 'latitude':
            return np.array([35, 34.75, 34.5])
        if name == 'longitude':
            return np.array([119, 119.25, 119.5, 119.75])
        if name == 'time':
            return self.origin + np.arange(744)
        h = int(key.split('.')[0])
        self.calls.append(h)
        if h == self.fail_hour:
            raise OSError('simulated network failure')
        return (np.arange(12, dtype='f4').reshape(1, 3, 4) - 1000 - h)

    def close(self):
        pass


def test_hourly_checkpoint_resume_and_receipt(tmp_path, monkeypatch):
    c = download_services.build_plan('era5', ['sshf'], [1993], [5], [119.25, 119.5, 34.5, 34.75])[0]
    FakeStore.calls = []
    FakeStore.fail_hour = 3
    original_fetch = era5_google.fetch
    monkeypatch.setattr(era5_google, 'fetch', lambda chunk, path: original_fetch(chunk, path, FakeStore))
    result = download_services._transfer_service((c, tmp_path))
    assert result['error_type'] == 'OSError'
    final = tmp_path / c['relative_path']
    assert not final.exists() and not final.with_suffix('.receipt.json').exists()
    checkpoint = final.with_suffix('.nc.part.google')
    with netCDF4.Dataset(checkpoint) as ds:
        assert ds.completed_hours == 3
    FakeStore.calls = []
    FakeStore.fail_hour = None
    result = download_services._transfer_service((c, tmp_path))
    assert result['state'] == 'downloaded'
    assert FakeStore.calls == list(range(3, 744))
    assert not checkpoint.exists()
    assert download_services.verify_service_file(final, c) == {'sshf': 744 * 4}
    with netCDF4.Dataset(final) as ds:
        np.testing.assert_array_equal(ds['sshf'][0], [[-995, -994], [-991, -990]])
        np.testing.assert_array_equal(ds['sshf'][743], [[-1738, -1737], [-1734, -1733]])
        assert ds['sshf'].units == 'J m**-2'
    receipt = json.loads(final.with_suffix('.receipt.json').read_text())
    assert receipt['source_url'] == era5_google.STORE
    assert receipt['download_provider'] == 'Google ARCO-ERA5'
    FakeStore.calls = []
    assert download_services._transfer_service((c, tmp_path))['state'] == 'verified_existing'
    assert FakeStore.calls == []


def test_old_cds_identity_and_receipt_skip_google(tmp_path, monkeypatch):
    c = next(c for c in download_all.group_plan(download_all.load_manifest()['groups']['P_ERA5'])
             if c['variables'] == ['sshf'] and c['period'] == '1996-11')
    # Literal path recorded from the pre-Google server download.
    assert c['relative_path'] == 'ERA5/sshf/1996/era5_hourly_1996-11_b51cabf79d5eeb4b.nc'
    final = download_data.safe_destination(tmp_path, c['relative_path'])
    download_data.ensure_collection(final, c)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b'old CDS fixture')
    download_data.write_json(final.with_suffix('.receipt.json'), {
        'request_sha256': c['request_sha256'], 'sha256': download_data.file_hash(final),
        'source_url': c['url'],
    })
    monkeypatch.setattr(era5_google, 'fetch', lambda *a: pytest.fail('must not redownload'))
    r = download_services._transfer_service((c, tmp_path))
    assert r['state'] == 'verified_existing' and r['source_url'] == c['url']


def test_wrong_units_rejected_before_writing(tmp_path):
    class BadStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.metadata['surface_sensible_heat_flux/.zattrs']['units'] = 'W m**-2'
    c = download_services.build_plan('era5', ['sshf'], [1993], [5], [119, 120, 34, 35])[0]
    with pytest.raises(download_data.DownloadError, match='units'):
        era5_google.fetch(c, tmp_path / 'bad.nc', BadStore)
    assert not list(tmp_path.iterdir())
