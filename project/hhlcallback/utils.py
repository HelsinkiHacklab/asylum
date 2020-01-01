# -*- coding: utf-8 -*-
import holviapi.utils


def get_nordea_payment_reference(member_id, tmatch):
    base = member_id + 1000
    return holviapi.utils.int2fin_reference(int("%s%s" % (base, tmatch)))


def get_nordea_yearly_payment_reference(member_id, tmatch, year):
    base = member_id + 1000
    return holviapi.utils.int2fin_reference(int("%s%s%s" % (base, tmatch, year)))
